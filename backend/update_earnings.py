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
CACHE_PATH = "backend/vol_cache.json"

FINNHUB_KEY = os.environ.get("FINNHUB_KEY")
TOKEN = os.environ.get("GH_TOKEN")
FMP_KEY = os.environ.get("FMP_KEY")

# ------------------------------------------------------------
# SAFE JSON WRAPPER
# ------------------------------------------------------------

def safe_json(url):
    try:
        r = requests.get(url, timeout=10)
        return r.json()
    except Exception:
        return None

# ------------------------------------------------------------
# LOAD / SAVE VOLATILITY CACHE
# ------------------------------------------------------------

def load_cache():
    try:
        with open(CACHE_PATH, "r") as f:
            return json.load(f)
    except:
        return {}

def save_cache(cache):
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)

def needs_refresh(entry):
    if "last_update" not in entry:
        return True
    last = datetime.strptime(entry["last_update"], "%Y-%m-%d")
    return (datetime.today() - last).days >= 3

# ------------------------------------------------------------
# VOLATILITY SIGNAL FETCHERS
# ------------------------------------------------------------

def fetch_iv(ticker):
    url = f"https://finnhub.io/api/v1/stock/option-chain?symbol={ticker}&token={FINNHUB_KEY}"
    r = safe_json(url)
    if not r:
        return None
    try:
        return r["data"][0]["implied_volatility"]
    except:
        return None

def fetch_last_earnings_move(ticker):
    end = int(datetime.now().timestamp())
    start = end - 86400 * 20

    url = f"https://finnhub.io/api/v1/stock/candle?symbol={ticker}&resolution=D&from={start}&to={end}&token={FINNHUB_KEY}"
    r = safe_json(url)
    if not r or r.get("s") != "ok":
        return None

    closes = r.get("c", [])
    if len(closes) < 3:
        return None

    return abs(closes[-1] - closes[-2]) / closes[-2]

def fetch_beta(ticker):
    url = f"https://finnhub.io/api/v1/stock/metric?symbol={ticker}&metric=all&token={FINNHUB_KEY}"
    r = safe_json(url)
    if not r:
        return None
    return r.get("metric", {}).get("beta")

def fetch_atr_ratio(ticker):
    end = int(datetime.now().timestamp())
    start = end - 86400 * 20

    url = f"https://finnhub.io/api/v1/stock/candle?symbol={ticker}&resolution=D&from={start}&to={end}&token={FINNHUB_KEY}"
    r = safe_json(url)
    if not r or r.get("s") != "ok":
        return None

    highs = r.get("h", [])
    lows = r.get("l", [])
    closes = r.get("c", [])

    if len(highs) < 15:
        return None

    trs = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        trs.append(tr)

    atr = sum(trs[-14:]) / 14
    return atr / closes[-1]

# ------------------------------------------------------------
# FETCH VOLATILITY (WITH CACHE)
# ------------------------------------------------------------

def fetch_volatility(ticker, cache):
    if ticker in cache and not needs_refresh(cache[ticker]):
        return cache[ticker]

    iv = fetch_iv(ticker)
    move = fetch_last_earnings_move(ticker)
    beta = fetch_beta(ticker)
    atr = fetch_atr_ratio(ticker)

    cache[ticker] = {
        "iv": iv,
        "move": move,
        "beta": beta,
        "atr": atr,
        "last_update": datetime.today().strftime("%Y-%m-%d")
    }

    return cache[ticker]

# ------------------------------------------------------------
# VOLATILITY SCORE (0–100)
# ------------------------------------------------------------

def compute_volatility_score(entry):
    iv = entry.get("iv") or 0
    move = entry.get("move") or 0
    beta = entry.get("beta") or 0
    atr = entry.get("atr") or 0

    iv_score = min(iv * 100, 100)
    move_score = min((move / 0.10) * 100, 100)
    beta_score = min((beta / 2.0) * 100, 100)
    atr_score = min((atr / 0.05) * 100, 100)

    return max(iv_score, move_score, beta_score, atr_score)

# ------------------------------------------------------------
# FETCH FROM FINNHUB
# ------------------------------------------------------------

def fetch_finnhub():
    start = (datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    end = (datetime.today() + timedelta(days=39)).strftime("%Y-%m-%d")

    url = f"https://finnhub.io/api/v1/calendar/earnings?from={start}&to={end}&token={FINNHUB_KEY}"
    r = requests.get(url)

    try:
        resp = r.json()
    except:
        print("Finnhub returned non‑JSON")
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

    print("Finnhub URL:", url)
    print("Finnhub response sample:", r.text[:200])
    print("Total Finnhub rows:", len(rows))

    return rows

# ------------------------------------------------------------
# FETCH FROM FMP (v4 endpoint)
# ------------------------------------------------------------

def fetch_fmp():
    url = f"https://financialmodelingprep.com/api/v4/earning-calendar?apikey={FMP_KEY}"
    r = safe_json(url)
    if not r:
        print("FMP returned empty or invalid JSON.")
        return []

    rows = []
    for item in r:
        if "symbol" in item and "date" in item:
            rows.append({
                "ticker": item["symbol"],
                "date": item["date"],
                "source": "FMP"
            })

    print("Total FMP rows:", len(rows))
    return rows

# ------------------------------------------------------------
# MERGE + ADD VOLATILITY
# ------------------------------------------------------------

def merge_sources():
    a = fetch_finnhub()
    b = fetch_fmp()

    # Option 4: closest future date merge
    def better_date(new_date_str, old_date_str):
        try:
            new = datetime.strptime(new_date_str, "%Y-%m-%d")
            old = datetime.strptime(old_date_str, "%Y-%m-%d")
        except:
            return False

        today = datetime.today()

        new_future = new >= today
        old_future = old >= today

        if new_future and old_future:
            return new < old

        if new_future and not old_future:
            return True

        if old_future and not new_future:
            return False

        return new > old

    merged = {}
    for row in a + b:
        ticker = row["ticker"]
        if ticker not in merged:
            merged[ticker] = row
        else:
            if better_date(row["date"], merged[ticker]["date"]):
                merged[ticker] = row

    merged_list = list(merged.values())

    cache = load_cache()
    today = datetime.today()

    def is_near_term(date_str):
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
            delta = (d - today).days
            return 0 <= delta <= 10
        except:
            return False

    MAX_VOL_TICKERS = 120
    count = 0

    for row in merged_list:
        if count < MAX_VOL_TICKERS and is_near_term(row["date"]):
            vol_entry = fetch_volatility(row["ticker"], cache)
            row["volatility_score"] = compute_volatility_score(vol_entry)
            count += 1
        else:
            row["volatility_score"] = 0

    save_cache(cache)

    merged_list.sort(key=lambda x: (x["date"], -x["volatility_score"]))

    return merged_list

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

    with open("earnings.json", "r") as f:
        content = f.read()

    encoded = base64.b64encode(content.encode()).decode()

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
