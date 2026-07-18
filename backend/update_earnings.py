import requests
import json
import base64
import os
from datetime import datetime, timedelta
# ------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------

GITHUB_USER = "baobao101"
REPO_NAME = "earnings-data"
FILE_PATH = "earnings.json"
TOKEN = os.environ.get("GH_TOKEN")   # GitHub Actions secret

# ------------------------------------------------------------
# FETCH FROM FINNHUB
# ------------------------------------------------------------

def fetch_finnhub():
    # Two months ago (≈60 days)
start = (datetime.today() - timedelta(days=60)).strftime("%Y-%m-%d")
end = datetime.today().strftime("%Y-%m-%d")
#end = (datetime.today() + timedelta(days=30)).strftime("%Y-%m-%d")

FINNHUB_KEY = os.environ.get("FINNHUB_KEY")
url = f"https://finnhub.io/api/v1/calendar/earnings?from={start}&to={end}&token={FINNHUB_KEY}"
   # FINNHUB_KEY = os.environ.get("FINNHUB_KEY")
   # url = f"https://finnhub.io/api/v1/calendar/earnings?from=2026-07-17&to=2026-12-31&token={FINNHUB_KEY}"
    r = requests.get(url)

    try:
        resp = r.json()
    except Exception:
        print("Finnhub returned non‑JSON or empty response.")
        return []

    data = resp.get("earningsCalendar") or []
    rows = []

    for item in data:
        if "symbol" in item and "date" in item:
            rows.append({
                "ticker": item["symbol"],
                "date": item["date"],
                "source": "Finnhub"
            })

    return rows
print("Finnhub URL:", url)
print("Finnhub response sample:", r.text[:200])


# ------------------------------------------------------------
# FETCH FROM EARNINGSAPI
# ------------------------------------------------------------

def fetch_earnings_api():
    url = "https://api.earningscalendar.net/?range=future"
    r = requests.get(url)

    try:
        resp = r.json()
    except Exception:
        print("EarningsAPI returned non‑JSON or empty response.")
        return []   # fail gracefully

    data = resp.get("results") or []
    rows = []

    for item in data:
        if "ticker" in item and "date" in item:
            rows.append({
                "ticker": item["ticker"],
                "date": item["date"],
                "source": "EarningsAPI"
            })

    return rows


# ------------------------------------------------------------
# MERGE SOURCES
# ------------------------------------------------------------

def merge_sources():
    a = fetch_finnhub()
    b = fetch_earnings_api()

    merged = {}
    for row in a + b:
        ticker = row["ticker"]
        if ticker not in merged:
            merged[ticker] = row
        else:
            # Keep the earliest future date
            if row["date"] < merged[ticker]["date"]:
                merged[ticker] = row

    return list(merged.values())

# ------------------------------------------------------------
# SAVE JSON LOCALLY
# ------------------------------------------------------------

def save_json(data):
    with open("earnings.json", "w") as f:
        json.dump(data, f, indent=2)

# ------------------------------------------------------------
# UPLOAD TO GITHUB
# ------------------------------------------------------------

def upload_json_to_github():
    url = f"https://api.github.com/repos/{GITHUB_USER}/{REPO_NAME}/contents/{FILE_PATH}"

    # Read local file
    with open("earnings.json", "r") as f:
        content = f.read()

    encoded = base64.b64encode(content.encode()).decode()

    # Check if file exists
    response = requests.get(url, headers={"Authorization": f"token {TOKEN}"})
    sha = response.json().get("sha") if response.status_code == 200 else None

    payload = {
        "message": "Daily earnings update",
        "content": encoded,
        "sha": sha
    }

    upload = requests.put(url, json=payload,
                          headers={"Authorization": f"token {TOKEN}"})

    print("Upload status:", upload.status_code, upload.text)

# ------------------------------------------------------------
# RUN
# ------------------------------------------------------------

if __name__ == "__main__":
    data = merge_sources()
    save_json(data)
    upload_json_to_github()
