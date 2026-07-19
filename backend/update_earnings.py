import os
import time
import json
import base64
import zipfile
import requests
import csv
from io import StringIO
from datetime import datetime, timedelta

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

# ------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------

def log(message: str):
    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"logs/{today}.log"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}\n"

    os.makedirs("logs", exist_ok=True)

    with open(filename, "a") as f:
        f.write(line)

    print(message)


# ------------------------------------------------------------
# RETRY WRAPPER
# ------------------------------------------------------------

def retry(operation, retries=3, delay=3, backoff=2, *args, **kwargs):
    current_delay = delay
    for attempt in range(1, retries + 1):
        try:
            log(f"Attempt {attempt} for {operation.__name__}")
            return operation(*args, **kwargs)
        except Exception as e:
            log(f"Attempt {attempt} failed: {e}")
            if attempt == retries:
                log(f"All retries failed for {operation.__name__}")
                raise
            time.sleep(current_delay)
            current_delay *= backoff


# ------------------------------------------------------------
# SAFE HTTP HELPERS
# ------------------------------------------------------------

def safe_json(url):
    try:
        r = requests.get(url, timeout=10)
        log(f"JSON URL: {url}")
        log(f"JSON status: {r.status_code}")
        log(f"JSON text sample: {r.text[:300]}")
        return r.json()
    except Exception as e:
        log(f"safe_json error: {e}")
        return None


def safe_get(url):
    try:
        r = requests.get(url, timeout=10)
        return r.json()
    except Exception as e:
        log(f"safe_get error: {e} URL: {url}")
        return None


# ------------------------------------------------------------
# VOLATILITY CACHE
# ------------------------------------------------------------

def load_cache():
    try:
        with open(CACHE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)


# ------------------------------------------------------------
# VOLATILITY SIGNAL FETCHERS
# ------------------------------------------------------------

def fetch_atr_ratio_alpha(ticker):
    url = (
        f"https://www.alphavantage.co/query?"
        f"function=ATR&symbol={ticker}&interval=daily&time_period=14&apikey={ALPHA_KEY}"
    )
    r = safe_get(url)
    if not r:
        return None

    try:
        ta = r["Technical Analysis: ATR"]
        first_key = list(ta.keys())[0]
        atr = float(ta[first_key]["ATR"])
        return atr
    except Exception as e:
        log(f"fetch_atr_ratio_alpha parse error: {e} ticker: {ticker}")
        return None


def fetch_price_alpha(ticker):
    url = (
        f"https://www.alphavantage.co/query?"
        f"function=GLOBAL_QUOTE&symbol={ticker}&apikey={ALPHA_KEY}"
    )
    r = safe_get(url)
    if not r:
        return None

    try:
        return float(r["Global Quote"]["05. price"])
    except Exception as e:
        log(f"fetch_price_alpha parse error: {e} ticker: {ticker}")
        return None


# ------------------------------------------------------------
# FETCH VOLATILITY (WITH CACHE)
# ------------------------------------------------------------

def fetch_volatility(ticker, cache):
    prev = cache.get(ticker, {})

    atr = fetch_atr_ratio_alpha(ticker)
    time.sleep(1)
    price = fetch_price_alpha(ticker)
    time.sleep(1)

    atr = atr if atr is not None else prev.get("atr")
    price = price if price is not None else prev.get("price")

    atr = atr if atr is not None else 0
    price = price if price is not None else 1

    move = prev.get("move")
    beta = prev.get("beta")

    cache[ticker] = {
        "iv": None,
        "move": move,
        "beta": beta,
        "atr": atr,
        "price": price,
        "last_update": datetime.today().strftime("%Y-%m-%d"),
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

    norm_atr = atr / price
    atr_score = min(norm_atr * 1000, 100)

    move_score = min(move * 100, 100) if move is not None else 0
    beta_score = min((beta / 2.0) * 100, 100) if beta is not None else 0

    return max(atr_score, move_score, beta_score)


# ------------------------------------------------------------
# FETCH FROM ALPHA (EARNINGS CALENDAR)
# ------------------------------------------------------------

def fetch_alpha_vantage():
    url = f"https://www.alphavantage.co/query?function=EARNINGS_CALENDAR&apikey={ALPHA_KEY}"
    r = requests.get(url, timeout=10)

    log(f"AlphaVantage status: {r.status_code}")
    log(f"AlphaVantage text sample: {r.text[:300]}")

    if r.status_code != 200:
        log(f"AlphaVantage returned non-200: {r.text[:200]}")
        return []

    try:
        f = StringIO(r.text)
        reader = csv.DictReader(f)
    except Exception as e:
        log(f"CSV parse error: {e}")
        return []

    rows = []
    for item in reader:
        symbol = item.get("symbol")
        date = item.get("reportDate")

        if symbol and date:
            rows.append(
                {
                    "ticker": symbol,
                    "date": date,
                    "source": "AlphaVantage",
                }
            )

    log(f"Total AlphaVantage rows: {len(rows)}")
    log(f"AlphaVantage raw sample: {rows[:5]}")
    return rows


# ------------------------------------------------------------
# FETCH FROM FINNHUB
# ------------------------------------------------------------

def fetch_finnhub():
    start = (datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    end = (datetime.today() + timedelta(days=30)).strftime("%Y-%m-%d")

    url = f"https://finnhub.io/api/v1/calendar/earnings?from={start}&to={end}&token={FINNHUB_KEY}"
    r = requests.get(url, timeout=10)

    try:
        resp = r.json()
    except Exception:
        log("Finnhub returned non‑JSON")
        return []

    data = resp.get("earningsCalendar") or []
    rows = []

    for item in data:
        if "symbol" in item and "date" in item:
            rows.append(
                {
                    "ticker": item["symbol"],
                    "date": item["date"],
                    "source": "Finnhub",
                }
            )

    log(f"Finnhub URL: {url}")
    log(f"Finnhub response sample: {r.text[:200]}")
    log(f"Total Finnhub rows: {len(rows)}")
    log(f"Finnhub first 5: {rows[:5]}")

    return rows


# ------------------------------------------------------------
# MERGE + ADD VOLATILITY
# ------------------------------------------------------------

def merge_sources(finnhub_rows, alpha_rows, cache):
    today = datetime.today()

    def is_near_term(date_str):
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
            return 0 <= (d - today).days <= 10
        except Exception:
            return False

    merged = {}

    for row in finnhub_rows:
        merged[row["ticker"]] = row

    for row in alpha_rows:
        ticker = row["ticker"]

        if ticker not in merged:
            merged[ticker] = row
            continue

        existing = merged[ticker]

        if existing["source"] == "Finnhub" and is_near_term(existing["date"]):
            continue

        merged[ticker] = row

    merged_list = list(merged.values())

    MAX_VOL_TICKERS = 120
    count = 0

    for row in merged_list:
        if is_near_term(row["date"]) and count < MAX_VOL_TICKERS:
            vol_entry = fetch_volatility(row["ticker"], cache)
            row["volatility_score"] = compute_volatility_score(vol_entry)
            count += 1
        else:
            row["volatility_score"] = 0

    merged_list.sort(key=lambda x: (x["date"], -x["volatility_score"]))
    log(f"Merged sample: {merged_list[:20]}")
    log(f"Cache sample: {list(cache.items())[:5]}")

    return merged_list


# ------------------------------------------------------------
# GRACEFUL MERGE WRAPPER
# ------------------------------------------------------------

def merge_gracefully(finnhub_data, alpha_data, cache):
    if finnhub_data and alpha_data:
        log("Both APIs available — merging normally")
        return merge_sources(finnhub_data, alpha_data, cache)

    if finnhub_data and not alpha_data:
        log("AlphaVantage unavailable — using Finnhub only")
        return merge_sources(finnhub_data, [], cache)

    if alpha_data and not finnhub_data:
        log("Finnhub unavailable — using AlphaVantage only")
        return merge_sources([], alpha_data, cache)

    log("Both APIs unavailable — no merged data")
    return None


# ------------------------------------------------------------
# HISTORY SNAPSHOTS
# ------------------------------------------------------------

def save_history(merged):
    os.makedirs("history", exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"history/{today}.json"

    with open(filename, "w") as f:
        json.dump(merged, f, indent=2)

    log(f"Saved history snapshot: {filename}")


def load_yesterday():
    history_dir = "history"
    if not os.path.exists(history_dir):
        return None

    files = sorted(os.listdir(history_dir))
    if not files:
        return None

    last_file = files[-1]
    with open(os.path.join(history_dir, last_file), "r") as f:
        data = json.load(f)

    log(f"Loaded yesterday snapshot: {last_file}")
    return data


def detect_staleness(today_rows, yesterday_rows):
    if yesterday_rows is None:
        return False, 0

    unchanged = 0
    yesterday_map = {r["ticker"]: r for r in yesterday_rows}

    for row in today_rows:
        t = row["ticker"]
        if t in yesterday_map:
            if row.get("date") == yesterday_map[t].get("date"):
                unchanged += 1

    total = len(today_rows) or 1
    ratio = unchanged / total

    return ratio > 0.80, unchanged


# ------------------------------------------------------------
# LOG ROTATION + COMPRESSION
# ------------------------------------------------------------

def compress_log_file(filepath):
    zip_path = filepath.replace(".log", ".zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        zipf.write(filepath, arcname=os.path.basename(filepath))

    os.remove(filepath)
    log(f"Compressed log file: {os.path.basename(filepath)} → {os.path.basename(zip_path)}")


def rotate_and_compress_logs(retention_days=30, compress_after_days=7):
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    now = datetime.now()
    cutoff_delete = now - timedelta(days=retention_days)
    cutoff_compress = now - timedelta(days=compress_after_days)

    for filename in os.listdir(log_dir):
        if not filename.endswith(".log") and not filename.endswith(".zip"):
            continue

        filepath = os.path.join(log_dir, filename)

        try:
            date_str = filename.split(".")[0]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")

            if filename.endswith(".zip") and file_date < cutoff_delete:
                os.remove(filepath)
                log(f"Deleted old compressed log: {filename}")
                continue

            if filename.endswith(".log") and file_date < cutoff_compress:
                compress_log_file(filepath)

        except Exception as e:
            log(f"Failed to process log file {filename}: {e}")


# ------------------------------------------------------------
# NOTIFICATIONS (STUBS)
# ------------------------------------------------------------

def notify_failure(error_message):
    log(f"Sending FAILURE notification: {error_message}")
    # hook into email / webhook here


def notify_success():
    log("Sending SUCCESS notification")
    # hook into email / webhook here


# ------------------------------------------------------------
# SAVE JSON LOCALLY
# ------------------------------------------------------------

def save_json(data):
    with open("earnings.json", "w") as f:
        json.dump(data, f, indent=2)
    log("Saved earnings.json locally")


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
        "sha": sha,
    }

    upload = requests.put(
        url,
        json=payload,
        headers={"Authorization": f"token {TOKEN}"},
    )

    log(f"Upload status: {upload.status_code} {upload.text}")


# ------------------------------------------------------------
# MAIN ORCHESTRATOR
# ------------------------------------------------------------

def main():
    log("=== Daily Earnings Sync Started ===")

    rotate_and_compress_logs(retention_days=30, compress_after_days=7)
    log("Log rotation + compression completed")

    yesterday = load_yesterday()

    try:
        log("Fetching Finnhub...")
        finnhub_data = retry(fetch_finnhub)
    except Exception as e:
        log(f"Finnhub failed: {e}")
        finnhub_data = None

    try:
        log("Fetching AlphaVantage...")
        alpha_data = retry(fetch_alpha_vantage)
    except Exception as e:
        log(f"AlphaVantage failed: {e}")
        alpha_data = None

    cache = load_cache()
    log("Merging data with graceful fallback...")
    merged = merge_gracefully(finnhub_data, alpha_data, cache)

    if merged is None:
        if yesterday is not None:
            log("Both APIs failed — using yesterday snapshot")
            data = yesterday
            notify_failure("Both APIs failed — uploaded yesterday snapshot")
        else:
            log("Both APIs failed and no history available — empty dataset")
            data = []
            notify_failure("Both APIs failed — no data available")
    else:
        log(f"Merged {len(merged)} tickers")
        save_history(merged)
        save_cache(cache)
        stale, unchanged = detect_staleness(merged, yesterday)
        if stale:
            log(f"Data appears stale — {unchanged} tickers unchanged vs yesterday")
        data = merged
        notify_success()

    save_json(data)
    upload_json_to_github()

    log("=== Sync Completed ===")


# ------------------------------------------------------------
# ENTRY POINT
# ------------------------------------------------------------

if __name__ == "__main__":
    main()
