import os
import sys
import json
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import find_dotenv, load_dotenv
from openai import AzureOpenAI
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

load_dotenv(find_dotenv())

DI_ENDPOINT         = os.getenv("DOCUMENTINTELLIGENCE_ENDPOINT", "").rstrip("/")
DI_KEY              = os.getenv("DOCUMENTINTELLIGENCE_API_KEY", "")
TRANSLATOR_ENDPOINT = os.getenv("AZURE_TRANSLATOR_ENDPOINT", "").rstrip("/")
TRANSLATOR_KEY      = os.getenv("AZURE_TRANSLATOR_KEY", "")
AOAI_ENDPOINT       = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
AOAI_KEY            = os.getenv("AZURE_OPENAI_KEY", "")
AOAI_DEPLOYMENT     = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

SUPPORTED_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"}

LANG_MAP: dict[str, str] = {
    "hindi":   "hi",
    "bengali": "bn",
    "tamil":   "ta",
    "telugu":  "te",
    "marathi": "mr",
    "kannada": "kn",
}

INVOICE_PROMPT = """You are an invoice data extraction assistant.

Extract the following fields from the invoice text below and return a valid JSON object.
If a field is not present, set its value to null.

Fields to extract:
- vendor_name
- vendor_address
- vendor_tax_id
- customer_name
- customer_address
- customer_id
- invoice_id
- invoice_date
- due_date
- purchase_order
- billing_address
- shipping_address
- subtotal
- total_tax
- invoice_total
- amount_due
- line_items: array of objects with fields: description, quantity, unit, unit_price, amount

Return ONLY the JSON object. No explanation, no markdown, no code block.

Invoice text:
{text}
"""


def _make_session() -> requests.Session:
    session = requests.Session()
    retry   = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    try:
        session.verify = certifi.where()
    except Exception:
        session.verify = False
    return session


HTTP = _make_session()


def _ocr_file(file_path: Path, di_client: DocumentIntelligenceClient) -> str:
    log.info("  OCR: %s", file_path.name)
    with open(file_path, "rb") as f:
        poller = di_client.begin_analyze_document(
            "prebuilt-read",
            body=f,
            content_type="application/octet-stream",
        )
    result = poller.result()

    lines: list[str] = []
    for page in result.pages or []:
        for line in page.lines or []:
            lines.append(line.content)
    return "\n".join(lines)


def _translate_to_english(text: str) -> str:
    if not text.strip():
        return ""

    log.info("  Translating to English...")
    url     = f"{TRANSLATOR_ENDPOINT}/translator/text/v3.0/translate"
    headers = {
        "Ocp-Apim-Subscription-Key": TRANSLATOR_KEY,
        "Content-Type": "application/json",
    }
    params = {"api-version": "3.0", "to": "en"}

    # Translator has a 50,000 char limit per request — chunk if needed
    chunks     = [text[i:i+45000] for i in range(0, len(text), 45000)]
    translated = []

    for chunk in chunks:
        resp = HTTP.post(url, headers=headers, params=params, json=[{"text": chunk}], timeout=30)
        resp.raise_for_status()
        translated.append(resp.json()[0]["translations"][0]["text"])

    return "\n".join(translated)


def _extract_fields_with_gpt(english_text: str, aoai_client: AzureOpenAI) -> dict:
    log.info("  Extracting fields with GPT...")
    response = aoai_client.chat.completions.create(
        model=AOAI_DEPLOYMENT,
        messages=[
            {"role": "user", "content": INVOICE_PROMPT.format(text=english_text)},
        ],
        temperature=0,
        max_tokens=2000,
    )

    raw = response.choices[0].message.content.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("  GPT returned non-JSON — saving raw text under 'raw_extraction'")
        return {"raw_extraction": raw}


def process_file(
    file_path: Path,
    di_client: DocumentIntelligenceClient,
    aoai_client: AzureOpenAI,
    local_out_dir: Path,
) -> Path:
    log.info("  File: %s", file_path.name)

    raw_text = _ocr_file(file_path, di_client)

    if not raw_text.strip():
        log.warning("  No text extracted from %s — skipping.", file_path.name)
        return None

    log.info("  Extracted %d chars.", len(raw_text))

    english_text = _translate_to_english(raw_text)
    data = _extract_fields_with_gpt(english_text, aoai_client)
    data["file"]         = file_path.name
    data["raw_text"]     = raw_text
    data["english_text"] = english_text

    out_path = local_out_dir / (file_path.stem + ".json")
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("  Saved: %s", out_path)

    return out_path


def process_folder(
    subfolder: Path,
    local_output_root: Path,
    di_client: DocumentIntelligenceClient,
    aoai_client: AzureOpenAI,
) -> list[Path]:
    files = [f for f in subfolder.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS]

    if not files:
        log.warning("No supported files in %s — skipping.", subfolder)
        return []

    log.info("[%s] %d file(s) found", subfolder.name, len(files))

    local_out_dir = local_output_root / subfolder.name
    local_out_dir.mkdir(parents=True, exist_ok=True)

    saved: list[Path] = []

    for file in files:
        try:
            out = process_file(file, di_client, aoai_client, local_out_dir)
            if out:
                saved.append(out)
        except Exception as exc:
            log.error("  %s failed: %s", file.name, exc)

    return saved


def run_extraction(
    input_root: str | Path,
    output_root: str | Path,
    max_workers: int = 3,
) -> None:
    input_root  = Path(input_root)
    output_root = Path(output_root)

    if not input_root.is_dir():
        log.error("Input root does not exist: %s", input_root)
        sys.exit(1)

    if not all([DI_ENDPOINT, DI_KEY]):
        log.error("Missing: DOCUMENTINTELLIGENCE_ENDPOINT, DOCUMENTINTELLIGENCE_API_KEY")
        sys.exit(1)

    if not all([TRANSLATOR_ENDPOINT, TRANSLATOR_KEY]):
        log.error("Missing: AZURE_TRANSLATOR_ENDPOINT, AZURE_TRANSLATOR_KEY")
        sys.exit(1)

    if not all([AOAI_ENDPOINT, AOAI_KEY]):
        log.error("Missing: AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY")
        sys.exit(1)

    subfolders = [e for e in sorted(input_root.iterdir()) if e.is_dir() and e.name.lower() in LANG_MAP]

    if not subfolders:
        files = [f for f in input_root.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS]
        if files:
            subfolders = [input_root]
        else:
            log.error("No recognisable language subfolders or supported files found in %s", input_root)
            sys.exit(1)

    log.info("Input  : %s", input_root)
    log.info("Output : %s", output_root)
    log.info("Folders: %d | Workers: %d", len(subfolders), max_workers)

    di_client   = DocumentIntelligenceClient(DI_ENDPOINT, AzureKeyCredential(DI_KEY))
    aoai_client = AzureOpenAI(azure_endpoint=AOAI_ENDPOINT, api_key=AOAI_KEY, api_version="2024-02-01")

    results: dict[str, list[Path]] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_folder = {
            executor.submit(process_folder, subfolder, output_root, di_client, aoai_client): subfolder.name
            for subfolder in subfolders
        }

        for future in as_completed(future_to_folder):
            folder_name = future_to_folder[future]
            try:
                saved = future.result()
                results[folder_name] = saved
                log.info("[%s] done — %d file(s).", folder_name, len(saved))
            except Exception as exc:
                log.error("[%s] failed: %s", folder_name, exc)
                results[folder_name] = []

    total = sum(len(p) for p in results.values())
    log.info("Total output files: %d → %s", total, output_root.resolve())


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        print("Usage: python invoice_extraction_test.py <input_root> [output_root] [max_workers]")
        print("Example: python invoice_extraction_test.py ../test_data ../outputs/trial4_invoice_extraction")
        sys.exit(0)

    _input_root  = args[0]
    _output_root = args[1] if len(args) > 1 else str(Path(args[0]).parent / "outputs" / "trial4_invoice_extraction")
    _max_workers = int(args[2]) if len(args) > 2 else 3

    run_extraction(_input_root, _output_root, _max_workers)
