import os
from pathlib import Path
from dotenv import load_dotenv
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential

load_dotenv()

DI_ENDPOINT = os.getenv("AZURE_VISION_ENDPOINT", "").rstrip("/")
DI_KEY = os.getenv("AZURE_VISION_KEY", "")

di_client = DocumentIntelligenceClient(DI_ENDPOINT, AzureKeyCredential(DI_KEY))

image_path = Path("../test_data/kannada/kannada invoice.jpg")
if not image_path.exists():
    files = list(Path("../test_data").rglob("*.jpg"))
    if files:
        image_path = files[0]

if image_path.exists():
    with open(image_path, "rb") as f:
        poller = di_client.begin_analyze_document(
            "prebuilt-layout",
            body=f,
            content_type="application/octet-stream",
            output_content_format="markdown"
        )
    result = poller.result()
    print("Content length:", len(result.content) if result.content else 0)
    print("Sample content:", result.content[:500] if result.content else "None")
else:
    print("No image found to test.")
