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

# Supported image formats
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".gif", ".webp"}

# API version that supports image translation
API_VERSION = "2025-12-01-preview"


def _parse_connection_string(conn_str: str) -> tuple[str, str]:
    account_name = account_key = ""
    for part in conn_str.split(";"):
        if part.startswith("AccountName="):
            account_name = part.split("=", 1)[1]
        elif part.startswith("AccountKey="):
            account_key = part.split("=", 1)[1]
    return account_name, account_key


def translate_image(input_image: str, target_lang: str = TARGET_LANG) -> None:
    input_path = Path(input_image)

    if input_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        print(f"Unsupported file type: {input_path.suffix}")
        print(f"Supported types: {', '.join(SUPPORTED_EXTENSIONS)}")
        sys.exit(1)

    output_path = input_path.parent / f"translated_{target_lang}_{input_path.name}"

    run_id        = uuid.uuid4().hex[:8]
    src_container = f"src-{run_id}"
    tgt_container = f"tgt-{run_id}"

    blob_service = BlobServiceClient.from_connection_string(CONN_STR)
    account_name, account_key = _parse_connection_string(CONN_STR)

    try:
        print("Creating temp containers...")
        blob_service.create_container(src_container)
        blob_service.create_container(tgt_container)

        print(f"Uploading {input_path.name}...")
        blob_client = blob_service.get_blob_client(container=src_container, blob=input_path.name)
        with open(input_path, "rb") as f:
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

        # Submit via REST API with the preview API version (required for image support)
        batch_url = f"{ENDPOINT}/translator/document:batch-translate?api-version={API_VERSION}"
        headers = {
            "Ocp-Apim-Subscription-Key": KEY,
            "Content-Type": "application/json",
        }
        payload = {
            "inputs": [
                {
                    "source": {
                        "sourceUrl": source_url,
                    },
                    "targets": [
                        {
                            "targetUrl": target_url,
                            "language": target_lang,
                        }
                    ],
                }
            ]
        }

        print(f"Submitting image translation job → {target_lang} (API {API_VERSION})...")
        response = requests.post(batch_url, headers=headers, json=payload)
        response.raise_for_status()

        # Job ID is returned in the Operation-Location header
        operation_location = response.headers.get("Operation-Location", "")
        if not operation_location:
            print("Error: No Operation-Location header in response.")
            sys.exit(1)

        job_id = operation_location.rstrip("/").split("/")[-1].split("?")[0]
        print(f"Job ID: {job_id}")

        # Poll for completion
        status_url = f"{ENDPOINT}/translator/document/batches/{job_id}?api-version={API_VERSION}"
        print("Waiting for translation to complete...")
        while True:
            status_resp = requests.get(status_url, headers={"Ocp-Apim-Subscription-Key": KEY})
            status_resp.raise_for_status()
            status_data = status_resp.json()
            status = status_data.get("status", "")
            print(f"  Status: {status}")

            if status in ("Succeeded", "Failed", "Cancelled"):
                break
            time.sleep(5)

        if status == "Succeeded":
            translated_blob = blob_service.get_blob_client(
                container=tgt_container, blob=input_path.name
            )
            with open(output_path, "wb") as out:
                out.write(translated_blob.download_blob().readall())
            print(f"Saved to: {output_path}")
        else:
            error = status_data.get("error", {})
            print(f"Translation failed: {error.get('message', 'unknown error')}")

    finally:
        print("Cleaning up temp containers...")
        try:
            blob_service.delete_container(src_container)
            blob_service.delete_container(tgt_container)
        except Exception:
            pass


if __name__ == "__main__":
    image_file = sys.argv[1] if len(sys.argv) > 1 else ""
    lang       = sys.argv[2] if len(sys.argv) > 2 else TARGET_LANG

    if not image_file or not Path(image_file).exists():
        print("Usage: python translate_single_image.py <image_file> [target_lang]")
        print("Example: python translate_single_image.py photo.png hi")
        print(f"Supported formats: {', '.join(SUPPORTED_EXTENSIONS)}")
        sys.exit(1)

    translate_image(image_file, lang)