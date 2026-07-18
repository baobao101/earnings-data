import base64
import json
import requests

# ------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------

GITHUB_USER = "baobao101"
REPO_NAME = "earnings-data"
FILE_PATH = "earnings.json"
TOKEN = "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"   # must have repo write access

# ------------------------------------------------------------
# SAMPLE DATA (replace with your real backend output)
# ------------------------------------------------------------

earnings = [
    {"ticker": "AAPL", "date": "2026-07-18", "source": "Finnhub"},
    {"ticker": "MSFT", "date": "2026-07-19", "source": "EarningsAPI"}
]

# ------------------------------------------------------------
# UPLOAD FUNCTION
# ------------------------------------------------------------

def upload_json_to_github(data):
    url = f"https://api.github.com/repos/{GITHUB_USER}/{REPO_NAME}/contents/{FILE_PATH}"

    # Convert JSON → string → base64
    json_str = json.dumps(data, indent=2)
    encoded = base64.b64encode(json_str.encode()).decode()

    # Check if file exists (needed to get SHA)
    response = requests.get(url, headers={"Authorization": f"token {TOKEN}"})

    if response.status_code == 200:
        sha = response.json()["sha"]
    else:
        sha = None  # new file

    payload = {
        "message": "Update earnings.json",
        "content": encoded,
        "sha": sha
    }

    upload = requests.put(url, json=payload,
                          headers={"Authorization": f"token {TOKEN}"})

    if upload.status_code in (200, 201):
        print("Uploaded successfully!")
    else:
        print("Upload failed:", upload.text)


# ------------------------------------------------------------
# RUN
# ------------------------------------------------------------

upload_json_to_github(earnings)

import json

def save_json(data):
    with open("earnings.json", "w") as f:
        json.dump(data, f, indent=2)
