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


def translate_image_batch(
    input_folder: str,
    output_folder: str,
    target_lang: str = TARGET_LANG,
    skip_existing: bool = True,
) -> None:
    input_path  = Path(input_folder)
    output_path = Path(output_folder)

    if not input_path.is_dir():
        print(f"Error: Input folder does not exist: {input_folder}")
        sys.exit(1)

    output_path.mkdir(parents=True, exist_ok=True)

    # Collect all supported images in the input folder
    image_files = [
        f for f in input_path.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not image_files:
        print(f"No supported image files found in: {input_folder}")
        print(f"Supported types: {', '.join(SUPPORTED_EXTENSIONS)}")
        sys.exit(0)

    print(f"Found {len(image_files)} image(s) in '{input_folder}'")
    print(f"Output folder : '{output_folder}'")
    print(f"Target language: {target_lang}\n")

    blob_service = BlobServiceClient.from_connection_string(CONN_STR)
    account_name, account_key = _parse_connection_string(CONN_STR)

    success_count = 0
    skip_count    = 0
    fail_count    = 0

    for idx, img_file in enumerate(image_files, start=1):
        out_file = output_path / f"translated_{target_lang}_{img_file.name}"

        if skip_existing and out_file.exists():
            print(f"[{idx}/{len(image_files)}] Skipping (already exists): {img_file.name}")
            skip_count += 1
            continue

        print(f"\n[{idx}/{len(image_files)}] Processing: {img_file.name}")

        run_id        = uuid.uuid4().hex[:8]
        src_container = f"src-{run_id}"
        tgt_container = f"tgt-{run_id}"

        try:
            print("  Creating temp containers...")
            blob_service.create_container(src_container)
            blob_service.create_container(tgt_container)

            print(f"  Uploading {img_file.name}...")
            blob_client = blob_service.get_blob_client(
                container=src_container, blob=img_file.name
            )
            with open(img_file, "rb") as f:
                blob_client.upload_blob(f, overwrite=True)

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

            batch_url = f"{ENDPOINT}/translator/document:batch-translate?api-version={API_VERSION}"
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

            print(f"  Submitting translation job → {target_lang}...")
            response = requests.post(batch_url, headers=headers, json=payload)
            response.raise_for_status()

            operation_location = response.headers.get("Operation-Location", "")
            if not operation_location:
                print("  Error: No Operation-Location header in response.")
                fail_count += 1
                continue

            job_id = operation_location.rstrip("/").split("/")[-1].split("?")[0]
            print(f"  Job ID: {job_id}")

            status_url = f"{ENDPOINT}/translator/document/batches/{job_id}?api-version={API_VERSION}"
            print("  Waiting for completion...", end="", flush=True)
            while True:
                status_resp = requests.get(
                    status_url, headers={"Ocp-Apim-Subscription-Key": KEY}
                )
                status_resp.raise_for_status()
                status_data = status_resp.json()
                status = status_data.get("status", "")
                print(f" {status}", end="", flush=True)

                if status in ("Succeeded", "Failed", "Cancelled"):
                    print()  # newline after status dots
                    break
                time.sleep(5)

            if status == "Succeeded":
                translated_blob = blob_service.get_blob_client(
                    container=tgt_container, blob=img_file.name
                )
                with open(out_file, "wb") as out:
                    out.write(translated_blob.download_blob().readall())
                print(f"  Saved: {out_file}")
                success_count += 1
            else:
                error = status_data.get("error", {})
                print(f"  Failed: {error.get('message', 'unknown error')}")
                fail_count += 1

        except Exception as e:
            print(f"  Exception while processing {img_file.name}: {e}")
            fail_count += 1

        finally:
            print("  Cleaning up temp containers...")
            try:
                blob_service.delete_container(src_container)
                blob_service.delete_container(tgt_container)
            except Exception:
                pass

    print(f"\n{'='*50}")
    print(f"Batch complete. "
          f"Success: {success_count} | Skipped: {skip_count} | Failed: {fail_count}")
    print(f"Translated images saved to: {output_path.resolve()}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python azure_batch_image_translation.py <input_folder> <output_folder> [target_lang]")
        print("Example: python azure_batch_image_translation.py ./images ./translated hi")
        print(f"Supported formats: {', '.join(SUPPORTED_EXTENSIONS)}")
        sys.exit(1)

    input_folder  = sys.argv[1]
    output_folder = sys.argv[2]
    lang          = sys.argv[3] if len(sys.argv) > 3 else TARGET_LANG

    translate_image_batch(input_folder, output_folder, lang)
