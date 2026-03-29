import os
import urllib.request
import urllib.error
from dotenv import load_dotenv
import json

load_dotenv()
key = os.environ.get("OPENAI_API_KEY")

print(f"Key starts with: {key[:15]}...")
print(f"Key length: {len(key)}")

try:
    req = urllib.request.Request("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"})
    r = urllib.request.urlopen(req)
    data = json.loads(r.read())
    print(f"SUCCESS! Models available: {len(data.get('data', []))}")
except urllib.error.HTTPError as e:
    print(f"ERROR CODE: {e.code}")
    print(f"ERROR BODY: {e.read().decode()}")
except Exception as e:
    print(f"OTHER ERROR: {e}")
