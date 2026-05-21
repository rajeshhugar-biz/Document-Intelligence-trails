import os
import requests
from dotenv import find_dotenv, load_dotenv
from pathlib import Path

load_dotenv(find_dotenv())

ENDPOINT = os.getenv("AZURE_TRANSLATOR_ENDPOINT", "").rstrip("/")
KEY = os.getenv("AZURE_TRANSLATOR_KEY", "")

url = f"{ENDPOINT}/translator/document/batches?api-version=2024-05-01"
print("Testing URL:", url)
response = requests.get(url, headers={"Ocp-Apim-Subscription-Key": KEY})
print("Status:", response.status_code)
print("Response:", response.text)

url2 = f"{ENDPOINT}/translator/document/batches?api-version=2025-12-01-preview"
print("Testing URL2:", url2)
response2 = requests.get(url2, headers={"Ocp-Apim-Subscription-Key": KEY})
print("Status:", response2.status_code)
print("Response:", response2.text)
