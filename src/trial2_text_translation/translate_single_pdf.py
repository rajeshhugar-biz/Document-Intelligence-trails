import os
import sys
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone

from dotenv import find_dotenv, load_dotenv
from azure.ai.translation.document import DocumentTranslationClient
from azure.core.credentials import AzureKeyCredential
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


def _parse_connection_string(conn_str: str) -> tuple[str, str]:
    account_name = account_key = ""
    for part in conn_str.split(";"):
        if part.startswith("AccountName="):
            account_name = part.split("=", 1)[1]
        elif part.startswith("AccountKey="):
            account_key = part.split("=", 1)[1]
    return account_name, account_key


def translate_pdf(input_pdf: str, target_lang: str = TARGET_LANG) -> None:
    input_path  = Path(input_pdf)
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

        print(f"Submitting translation job → {target_lang}...")
        client = DocumentTranslationClient(ENDPOINT, AzureKeyCredential(KEY))
        poller = client.begin_translation(source_url, target_url, target_lang)
        print(f"Job ID: {poller.id}")

        print("Waiting for translation to complete...")
        result = poller.result()

        for doc in result:
            if doc.status == "Succeeded":
                translated_blob = blob_service.get_blob_client(
                    container=tgt_container, blob=input_path.name
                )
                with open(output_path, "wb") as out:
                    out.write(translated_blob.download_blob().readall())
                print(f"Saved to: {output_path}")
            elif doc.status == "Failed":
                print(f"Translation failed: {doc.error.message if doc.error else 'unknown error'}")

    finally:
        print("Cleaning up temp containers...")
        try:
            blob_service.delete_container(src_container)
            blob_service.delete_container(tgt_container)
        except Exception:
            pass


if __name__ == "__main__":
    pdf_file = sys.argv[1] if len(sys.argv) > 1 else ""
    lang     = sys.argv[2] if len(sys.argv) > 2 else TARGET_LANG

    if not pdf_file or not Path(pdf_file).exists():
        print("Usage: python translate_single_pdf.py <file.pdf> [target_lang]")
        print("Example: python translate_single_pdf.py report.pdf hi")
        sys.exit(1)

    translate_pdf(pdf_file, lang)
