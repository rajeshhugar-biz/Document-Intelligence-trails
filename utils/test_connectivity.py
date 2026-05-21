import requests

r = requests.get("https://api.cognitive.microsofttranslator.com")
print(r.status_code)