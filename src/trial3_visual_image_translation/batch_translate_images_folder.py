import os
import sys
import uuid
import time
import requests
from pathlib import Path
from datetime import datetime, timedelta, timezone

from dotenv import find_dotenv, load_dotenv
from azure.storage.blob import (
    BlobServiceClient,
    generate_container_sas,
    ContainerSasPermissions,
)

load_dotenv(find_dotenv())

ENDPOINT    = os.getenv("AZURE_TRANSLATOR_ENDPOINT", "").rstrip("/")
KEY         = os.getenv("AZURE_TRANSLATOR_KEY", "")
CONN_STR    = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
TARGET_LANG = "en"

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".gif", ".webp"}
API_VERSION = "2025-12-01-preview"

def _parse_connection_string(conn_str: str) -> tuple[str, str]:
    account_name = account_key = ""
    for part in conn_str.split(";"):
        if part.startswith("AccountName="):
            account_name = part.split("=", 1)[1]
        elif part.startswith("AccountKey="):
            account_key = part.split("=", 1)[1]
    return account_name, account_key

def safe_makedirs(path: Path) -> None:
    """
    Safely create a directory at `path`.
    - If `path` already exists as a directory: do nothing (all good).
    - If `path` exists as a FILE: delete the file first, then create the directory.
      This fixes the Python bug where mkdir(..., exist_ok=True) still raises
      FileExistsError when the path exists as a file rather than a folder.
    - Otherwise: create the full directory tree.
    """
    if path.is_dir():
        return  # already a directory, nothing to do
    if path.exists():
        print(f"  Warning: '{path}' exists as a file — removing it to create directory.")
        path.unlink()  # delete the stale file
    path.mkdir(parents=True, exist_ok=True)

def translate_folder(input_dir: Path, output_dir: Path, target_lang: str = TARGET_LANG):
    if not input_dir.exists():
        print(f"Input directory {input_dir} does not exist.")
        return

    images_to_process = []
    for root, _, files in os.walk(input_dir):
        for file in files:
            path = Path(root) / file
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                images_to_process.append(path)

    if not images_to_process:
        print(f"No supported images found in {input_dir}.")
        return

    print(f"Found {len(images_to_process)} images to process.")

    run_id        = uuid.uuid4().hex[:8]
    src_container = f"src-{run_id}"
    tgt_container = f"tgt-{run_id}"

    blob_service = BlobServiceClient.from_connection_string(CONN_STR)
    account_name, account_key = _parse_connection_string(CONN_STR)

    try:
        print("Creating temp containers...")
        blob_service.create_container(src_container)
        blob_service.create_container(tgt_container)

        print("Uploading images to source container...")
        for img_path in images_to_process:
            relative_path = img_path.relative_to(input_dir).as_posix()
            blob_client = blob_service.get_blob_client(container=src_container, blob=relative_path)
            with open(img_path, "rb") as f:
                blob_client.upload_blob(f, overwrite=True)
            print(f"  Uploaded: {relative_path}")

        expiry = datetime.now(timezone.utc) + timedelta(hours=4)
        src_sas = generate_container_sas(
            account_name=account_name, account_key=account_key,
            container_name=src_container,
            permission=ContainerSasPermissions(read=True, list=True),
            expiry=expiry,
        )
        tgt_sas = generate_container_sas(
            account_name=account_name, account_key=account_key,
            container_name=tgt_container,
            permission=ContainerSasPermissions(read=True, write=True, list=True, delete=True),
            expiry=expiry,
        )

        source_url = f"https://{account_name}.blob.core.windows.net/{src_container}?{src_sas}"
        target_url = f"https://{account_name}.blob.core.windows.net/{tgt_container}?{tgt_sas}"

        batch_url = f"{ENDPOINT}/translator/document/batches?api-version={API_VERSION}"
        headers = {
            "Ocp-Apim-Subscription-Key": KEY,
            "Content-Type": "application/json",
        }
        payload = {
            "inputs": [
                {
                    "source": {"sourceUrl": source_url},
                    "targets": [{"targetUrl": target_url, "language": target_lang}],
                }
            ]
        }

        print(f"\nSubmitting batch image translation job -> {target_lang} (API {API_VERSION})...")
        response = requests.post(batch_url, headers=headers, json=payload)
        response.raise_for_status()

        operation_location = response.headers.get("Operation-Location", "")
        if not operation_location:
            print("Error: No Operation-Location header in response.")
            sys.exit(1)

        job_id = operation_location.rstrip("/").split("/")[-1].split("?")[0]
        print(f"Job ID: {job_id}")

        status_url = f"{ENDPOINT}/translator/document/batches/{job_id}?api-version={API_VERSION}"
        print("Waiting for batch translation to complete (this may take a few minutes)...")

        while True:
            status_resp = requests.get(status_url, headers={"Ocp-Apim-Subscription-Key": KEY})
            status_resp.raise_for_status()
            status_data = status_resp.json()
            status = status_data.get("status", "")

            summary     = status_data.get("summary", {})
            successes   = summary.get("success", 0)
            failures    = summary.get("failed", 0)
            in_progress = summary.get("inProgress", 0)
            total       = summary.get("total", 0)

            print(f"  Status: {status} | Progress: {successes} succeeded, {failures} failed, {in_progress} in progress (Total: {total})")

            if status in ("Succeeded", "Failed", "Cancelled", "ValidationFailed"):
                break
            time.sleep(10)

        print("\nDownloading translated images...")

        # ── FIX: use safe_makedirs instead of output_dir.mkdir() ──────────────
        safe_makedirs(output_dir)

        tgt_container_client = blob_service.get_container_client(tgt_container)
        blobs = tgt_container_client.list_blobs()

        download_count = 0
        for blob in blobs:
            blob_name = blob.name

            if blob_name.endswith('/'):
                continue

            local_file_path = output_dir / blob_name

            # Use safe_makedirs for each blob's parent folder too
            safe_makedirs(local_file_path.parent)

            if local_file_path.is_dir():
                continue

            blob_client = tgt_container_client.get_blob_client(blob=blob_name)
            with open(local_file_path, "wb") as out:
                out.write(blob_client.download_blob().readall())
            print(f"  Saved: {local_file_path}")
            download_count += 1

        print(f"\nSuccessfully downloaded {download_count} translated images to {output_dir.resolve()}")

    finally:
        print("\nCleaning up temp containers...")
        try:
            blob_service.delete_container(src_container)
            blob_service.delete_container(tgt_container)
            print("Cleanup complete.")
        except Exception as e:
            print(f"Cleanup failed: {e}")

if __name__ == "__main__":
    src_dir    = Path(__file__).parent
    input_dir  = src_dir.parent / "test_data"
    output_dir = src_dir.parent.parent / "outputs" / "trial3_visual_image_translation"

    if not all([ENDPOINT, KEY, CONN_STR]):
        print("Missing required environment variables in .env.")
        sys.exit(1)

    translate_folder(input_dir, output_dir, target_lang=TARGET_LANG)
