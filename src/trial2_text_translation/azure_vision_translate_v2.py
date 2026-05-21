import os
import sys
import uuid
import time
import shutil
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import find_dotenv, load_dotenv
from azure.ai.translation.document import DocumentTranslationClient
from azure.core.credentials import AzureKeyCredential
from azure.storage.blob import (
    BlobServiceClient,
    generate_container_sas,
    ContainerSasPermissions,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

load_dotenv(find_dotenv())

ENDPOINT        = os.getenv("AZURE_TRANSLATOR_ENDPOINT", "").rstrip("/")
KEY             = os.getenv("AZURE_TRANSLATOR_KEY", "")
CONN_STR        = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
OUT_CONTAINER   = os.getenv("AZURE_OUTPUT_CONTAINER", "translated-output")
VISION_ENDPOINT = os.getenv("AZURE_VISION_ENDPOINT", "").rstrip("/")
VISION_KEY      = os.getenv("AZURE_VISION_KEY", "")

PDF_EXTS   = {".pdf"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".gif", ".webp"}
ALL_EXTS   = PDF_EXTS | IMAGE_EXTS

LANG_MAP: dict[str, str] = {
    "hindi":      "hi",
    "bengali":    "bn",
    "tamil":      "ta",
    "telugu":     "te",
    "marathi":    "mr",
    "kannada":    "kn"
}


def _make_session() -> requests.Session:
    session = requests.Session()
    retry   = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    try:
        session.verify = certifi.where()
    except Exception:
        log.warning("certifi CA bundle not found — falling back to verify=False")
        session.verify = False
    return session


HTTP = _make_session()


def _parse_connection_string(conn_str: str) -> tuple[str, str]:
    account_name = account_key = ""
    for part in conn_str.split(";"):
        if part.startswith("AccountName="):
            account_name = part.split("=", 1)[1]
        elif part.startswith("AccountKey="):
            account_key = part.split("=", 1)[1]
    return account_name, account_key


def _ensure_container(blob_service: BlobServiceClient, container: str) -> None:
    try:
        blob_service.create_container(container)
        log.info("Created container: %s", container)
    except Exception:
        pass


def _upload_blob(
    blob_service: BlobServiceClient,
    container: str,
    blob_path: str,
    local_path: Path,
) -> None:
    client = blob_service.get_blob_client(container=container, blob=blob_path)
    with open(local_path, "rb") as f:
        client.upload_blob(f, overwrite=True)
    log.info("  Uploaded → %s/%s", container, blob_path)


def _ocr_image(image_path: Path) -> str:
    if not VISION_ENDPOINT or not VISION_KEY:
        raise EnvironmentError("AZURE_VISION_ENDPOINT and AZURE_VISION_KEY must be set in .env")

    read_url = f"{VISION_ENDPOINT}/vision/v3.2/read/analyze"
    headers  = {
        "Ocp-Apim-Subscription-Key": VISION_KEY,
        "Content-Type": "application/octet-stream",
    }

    with open(image_path, "rb") as f:
        resp = HTTP.post(read_url, headers=headers, data=f, timeout=30)
    resp.raise_for_status()

    operation_url = resp.headers["Operation-Location"]
    poll_headers  = {"Ocp-Apim-Subscription-Key": VISION_KEY}

    for _ in range(30):
        time.sleep(1)
        poll = HTTP.get(operation_url, headers=poll_headers, timeout=15)
        poll.raise_for_status()
        data   = poll.json()
        status = data.get("status", "")

        if status == "succeeded":
            lines: list[str] = []
            for page in data.get("analyzeResult", {}).get("readResults", []):
                for line in page.get("lines", []):
                    lines.append(line.get("text", ""))
            return "\n".join(lines)

        if status == "failed":
            raise RuntimeError(f"Azure Vision OCR failed for {image_path.name}")

    raise TimeoutError(f"Azure Vision OCR timed out for {image_path.name}")


def _translate_text(text: str, target_lang: str) -> str:
    if not text.strip():
        return ""

    url     = f"{ENDPOINT}/translator/text/v3.0/translate"
    headers = {
        "Ocp-Apim-Subscription-Key": KEY,
        "Content-Type": "application/json",
    }
    params = {"api-version": "3.0", "to": target_lang}
    body   = [{"text": text}]

    resp = HTTP.post(url, headers=headers, params=params, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()[0]["translations"][0]["text"]


def process_image(
    image_path: Path,
    local_out_dir: Path,
    blob_service: BlobServiceClient,
    folder_name: str,
) -> list[Path]:
    log.info("  Image: %s", image_path.name)
    saved: list[Path] = []

    out_image = local_out_dir / image_path.name
    shutil.copy2(image_path, out_image)
    _upload_blob(blob_service, OUT_CONTAINER, f"{folder_name}/{image_path.name}", out_image)
    saved.append(out_image)

    log.info("  OCR in progress...")
    extracted = _ocr_image(image_path)

    if not extracted.strip():
        log.warning("  No text detected in %s — skipping translation.", image_path.name)
        return saved

    log.info("  Extracted %d chars.", len(extracted))
    log.info("  Translating → en...")
    translated = _translate_text(extracted, "en")

    txt_name = image_path.stem + ".txt"
    out_txt  = local_out_dir / txt_name
    out_txt.write_text(translated, encoding="utf-8")
    log.info("  Saved: %s", out_txt)

    _upload_blob(blob_service, OUT_CONTAINER, f"{folder_name}/{txt_name}", out_txt)
    saved.append(out_txt)

    return saved


def process_pdfs(
    pdf_files: list[Path],
    target_lang: str,
    local_out_dir: Path,
    blob_service: BlobServiceClient,
    account_name: str,
    account_key: str,
    folder_name: str,
) -> list[Path]:
    run_id        = uuid.uuid4().hex[:8]
    src_container = f"src-{run_id}"
    tgt_container = f"tgt-{run_id}"
    saved: list[Path] = []

    try:
        blob_service.create_container(src_container)
        blob_service.create_container(tgt_container)
        log.info("  Temp containers: %s / %s", src_container, tgt_container)

        for pdf in pdf_files:
            bc = blob_service.get_blob_client(container=src_container, blob=pdf.name)
            with open(pdf, "rb") as f:
                bc.upload_blob(f, overwrite=True)
            log.info("  Uploaded: %s", pdf.name)

        expiry = datetime.now(timezone.utc) + timedelta(hours=2)
        src_sas = generate_container_sas(
            account_name=account_name, account_key=account_key,
            container_name=src_container,
            permission=ContainerSasPermissions(read=True, list=True),
            expiry=expiry,
        )
        tgt_sas = generate_container_sas(
            account_name=account_name, account_key=account_key,
            container_name=tgt_container,
            permission=ContainerSasPermissions(read=True, write=True, list=True),
            expiry=expiry,
        )

        source_url = f"https://{account_name}.blob.core.windows.net/{src_container}?{src_sas}"
        target_url = f"https://{account_name}.blob.core.windows.net/{tgt_container}?{tgt_sas}"

        log.info("  Submitting PDF batch job → %s...", target_lang)
        translator = DocumentTranslationClient(ENDPOINT, AzureKeyCredential(KEY))
        poller     = translator.begin_translation(source_url, target_url, target_lang)
        log.info("  Job ID: %s", poller.id)

        log.info("  Waiting for PDF translation...")
        result = poller.result()

        for doc in result:
            if doc.status == "Succeeded":
                blob_name  = Path(doc.translated_document.url).name.split("?")[0]
                local_path = local_out_dir / blob_name

                data = blob_service.get_blob_client(
                    container=tgt_container, blob=blob_name
                ).download_blob().readall()

                with open(local_path, "wb") as out:
                    out.write(data)
                log.info("  Saved PDF: %s", local_path)

                _upload_blob(blob_service, OUT_CONTAINER, f"{folder_name}/{blob_name}", local_path)
                saved.append(local_path)

            elif doc.status == "Failed":
                log.error(
                    "  PDF failed — %s: %s",
                    doc.source_document_url,
                    doc.error.message if doc.error else "unknown error",
                )

    finally:
        for c in (src_container, tgt_container):
            try:
                blob_service.delete_container(c)
            except Exception:
                pass
        log.info("  Cleaned up temp containers.")

    return saved


def translate_folder(
    subfolder: Path,
    target_lang: str,
    local_output_root: Path,
    blob_service: BlobServiceClient,
    account_name: str,
    account_key: str,
) -> list[Path]:
    all_files = [f for f in subfolder.iterdir() if f.is_file() and f.suffix.lower() in ALL_EXTS]
    pdf_files = [f for f in all_files if f.suffix.lower() in PDF_EXTS]
    img_files = [f for f in all_files if f.suffix.lower() in IMAGE_EXTS]

    if not all_files:
        log.warning("  No supported files in %s — skipping.", subfolder)
        return []

    log.info("[%s] → lang=%s | %d PDF(s), %d image(s)", subfolder.name, target_lang, len(pdf_files), len(img_files))

    local_out_dir = local_output_root / subfolder.name
    local_out_dir.mkdir(parents=True, exist_ok=True)

    saved: list[Path] = []

    if pdf_files:
        saved += process_pdfs(pdf_files, target_lang, local_out_dir, blob_service, account_name, account_key, subfolder.name)

    for img in img_files:
        try:
            saved += process_image(img, local_out_dir, blob_service, subfolder.name)
        except Exception as exc:
            log.error("  Image %s failed: %s", img.name, exc)

    return saved


def run_batch(input_root: str | Path, output_root: str | Path, max_workers: int = 3) -> None:
    input_root  = Path(input_root)
    output_root = Path(output_root)

    if not input_root.is_dir():
        log.error("Input root does not exist: %s", input_root)
        sys.exit(1)

    if not all([ENDPOINT, KEY, CONN_STR]):
        log.error("Missing env vars: AZURE_TRANSLATOR_ENDPOINT, AZURE_TRANSLATOR_KEY, AZURE_STORAGE_CONNECTION_STRING")
        sys.exit(1)

    subfolders: list[tuple[Path, str]] = []
    for entry in sorted(input_root.iterdir()):
        if not entry.is_dir():
            continue
        lang_code = LANG_MAP.get(entry.name.lower())
        if lang_code:
            subfolders.append((entry, lang_code))
        else:
            log.warning("Subfolder '%s' not in LANG_MAP — skipping.", entry.name)

    if not subfolders:
        log.error("No recognisable language subfolders found in %s", input_root)
        sys.exit(1)

    log.info("Input  : %s", input_root)
    log.info("Output : %s", output_root)
    log.info("Folders: %d | Workers: %d", len(subfolders), max_workers)

    blob_service = BlobServiceClient.from_connection_string(CONN_STR)
    account_name, account_key = _parse_connection_string(CONN_STR)
    _ensure_container(blob_service, OUT_CONTAINER)

    results: dict[str, list[Path]] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_folder = {
            executor.submit(
                translate_folder,
                subfolder, lang_code, output_root,
                blob_service, account_name, account_key,
            ): subfolder.name
            for subfolder, lang_code in subfolders
        }

        for future in as_completed(future_to_folder):
            folder_name = future_to_folder[future]
            try:
                saved = future.result()
                results[folder_name] = saved
                log.info("[%s] done — %d output file(s).", folder_name, len(saved))
            except Exception as exc:
                log.error("[%s] failed: %s", folder_name, exc)
                results[folder_name] = []

    total = sum(len(p) for p in results.values())
    log.info("Total output files: %d → %s", total, output_root.resolve())


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        print("Usage: python azure_vision_translate_v2.py <input_root> [output_root] [max_workers]")
        sys.exit(0)

    _input_root  = args[0]
    _output_root = args[1] if len(args) > 1 else str(Path(args[0]).parent / "outputs" / "trial2_text_translation")
    _max_workers = int(args[2]) if len(args) > 2 else 3

    run_batch(_input_root, _output_root, _max_workers)
