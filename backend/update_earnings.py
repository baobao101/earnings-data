import requests
import json
import base64
import os
from datetime import datetime, timedelta
import csv
from io import StringIO
import time
# ------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------

GITHUB_USER = "baobao101"
REPO_NAME = "earnings-data"
FILE_PATH = "earnings.json"
CACHE_PATH = "backend/vol_cache.json"
ALPHA_KEY = os.environ.get("ALPHA_KEY")

FINNHUB_KEY = os.environ.get("FINNHUB_KEY")
TOKEN = os.environ.get("GH_TOKEN")

def safe_json(url):
    try:
        r = requests.get(url, timeout=10)
        print("JSON URL:", url)
        print("JSON status:", r.status_code)
        print("JSON text sample:", r.text[:300])
        return r.json()
    except Exception as e:
        print("safe_json error:", e)
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
    return True  # always refresh near-term


# ------------------------------------------------------------
# VOLATILITY SIGNAL FETCHERS
# ------------------------------------------------------------


def fetch_volatility_alpha(ticker):
    url = f"https://www.alphavantage.co/query?function=VOLATILITY&symbol={ticker}&interval=daily&apikey={ALPHA_KEY}"
    r = requests.get(url, timeout=10).json()

    try:
        vol = float(r["Volatility"]["Volatility"])
        return vol
    except:
        return None


def fetch_beta_alpha(ticker):
    url = f"https://www.alphavantage.co/query?function=BETA&symbol={ticker}&apikey={ALPHA_KEY}"
    r = requests.get(url, timeout=10).json()

    try:
        return float(r["Beta"]["beta"])
    except:
        return None

def fetch_atr_ratio_alpha(ticker):
    url = f"https://www.alphavantage.co/query?function=ATR&symbol={ticker}&interval=daily&time_period=14&apikey={ALPHA_KEY}"
    r = requests.get(url, timeout=10).json()

    try:
        atr = float(r["Technical Analysis: ATR"][list(r["Technical Analysis: ATR"].keys())[0]]["ATR"])
        return atr
    except:
        return None



# ------------------------------------------------------------
# FETCH VOLATILITY (WITH CACHE)
# ------------------------------------------------------------
def fetch_volatility(ticker, cache):
    prev = cache.get(ticker, {})

    vol = fetch_volatility_alpha(ticker)
    time.sleep(1)
    beta = fetch_beta_alpha(ticker)
    time.sleep(1)
    atr = fetch_atr_ratio_alpha(ticker)
    time.sleep(1)
    price = fetch_price_alpha(ticker)
    time.sleep(1)

    # Fallbacks
    vol = vol if vol is not None else prev.get("move")
    beta = beta if beta is not None else prev.get("beta")
    atr = atr if atr is not None else prev.get("atr")
    price = price if price is not None else prev.get("price")

    # Final safety fallback
    atr = atr if atr is not None else 0
    price = price if price is not None else 1  # avoid division by zero

    # Save
    cache[ticker] = {
        "iv": None,
        "move": vol,
        "beta": beta,
        "atr": atr,
        "price": price,
        "last_update": datetime.today().strftime("%Y-%m-%d")
    }

    return cache[ticker]





# ------------------------------------------------------------
# VOLATILITY SCORE (0–100)
# ------------------------------------------------------------

def compute_volatility_score(entry):
    atr = entry.get("atr") or 0
    price = entry.get("price") or 1
    move = entry.get("move")
    beta = entry.get("beta")

    # Normalized ATR (percentage)
    norm_atr = atr / price

    # ATR score (0–100)
    atr_score = min(norm_atr * 1000, 100)  # 0.02 → 20, 0.05 → 50, 0.10 → 100

    # Optional signals
    move_score = min(move * 100, 100) if move is not None else 0
    beta_score = min((beta / 2.0) * 100, 100) if beta is not None else 0

    return max(atr_score, move_score, beta_score)







# ------------------------------------------------------------
# FETCH FROM ALPHA
# ------------------------------------------------------------
def fetch_alpha_vantage():
    url = f"https://www.alphavantage.co/query?function=EARNINGS_CALENDAR&apikey={ALPHA_KEY}"
    r = requests.get(url, timeout=10)

    print("AlphaVantage status:", r.status_code)
    print("AlphaVantage text sample:", r.text[:300])

    if r.status_code != 200:
        print("AlphaVantage returned non-200:", r.text[:200])
        return []

    # Parse CSV
    try:
        f = StringIO(r.text)
        reader = csv.DictReader(f)
    except Exception as e:
        print("CSV parse error:", e)
        return []

    rows = []
    for item in reader:
        symbol = item.get("symbol")
        date = item.get("reportDate")

        if symbol and date:
            rows.append({
                "ticker": symbol,
                "date": date,
                "source": "AlphaVantage"
            })

    print("Total AlphaVantage rows:", len(rows))
    print("AlphaVantage raw sample:", rows[:5])
    return rows

# ------------------------------------------------------------
# FETCH FROM alpha price to normalize atr
# ------------------------------------------------------------
def fetch_price_alpha(ticker):
    url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={ticker}&apikey={ALPHA_KEY}"
    r = requests.get(url, timeout=10).json()

    try:
        return float(r["Global Quote"]["05. price"])
    except:
        return None


# ------------------------------------------------------------
# FETCH FROM FINNHUB
# ------------------------------------------------------------

def fetch_finnhub():
    start = (datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    end = (datetime.today() + timedelta(days=30)).strftime("%Y-%m-%d")

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
    print("Finnhub first 5:", rows[:5])   # FIXED

    return rows


# ------------------------------------------------------------
# MERGE + ADD VOLATILITY
# ------------------------------------------------------------

def merge_sources():
    a = fetch_finnhub()
    #b = fetch_eodhd()
    #b = fetch_polygon()
    #b = fetch_yahoo()
    b = fetch_alpha_vantage()

    #b = fetch_fmp()

    today = datetime.today()

    def is_near_term(date_str):
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
            return 0 <= (d - today).days <= 10
        except:
            return False

    merged = {}

    # First load Finnhub
    for row in a:
        merged[row["ticker"]] = row

    # Then merge FMP with rules:
    # - If Finnhub is near-term, keep Finnhub
    # - Otherwise prefer FMP
    for row in b:
        ticker = row["ticker"]

        if ticker not in merged:
            merged[ticker] = row
            continue

        existing = merged[ticker]

        # Finnhub near-term → keep Finnhub
        if existing["source"] == "Finnhub" and is_near_term(existing["date"]):
            continue

        # Otherwise FMP overrides
        merged[ticker] = row

    merged_list = list(merged.values())

    # Volatility scoring
    cache = load_cache()
    MAX_VOL_TICKERS = 120
    count = 0

    for row in merged_list:
        if is_near_term(row["date"]) and count < MAX_VOL_TICKERS:
            vol_entry = fetch_volatility(row["ticker"], cache)
            row["volatility_score"] = compute_volatility_score(vol_entry)
            count += 1
        else:
            row["volatility_score"] = 0


    save_cache(cache)

    merged_list.sort(key=lambda x: (x["date"], -x["volatility_score"]))
    print("Merged sample:", merged_list[:20])
    print("Cache sample:", list(load_cache().items())[:5])

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
