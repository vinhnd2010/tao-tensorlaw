#!/usr/bin/env python3
"""TAO Tensor Law Dashboard - Self-hosted on port 8086"""

import json
import math
import os
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__, template_folder=".")

DATA_DIR = Path(__file__).parent
CACHE_FILE = DATA_DIR / "price_data.json"
CACHE_MAX_AGE = 86400  # 24 hours

OFFSET_NAKAMOTO = 486


def fetch_price_data():
    """Fetch price_data.json from taotensorlaw.com, cache locally."""
    needs_fetch = True
    if CACHE_FILE.exists():
        age = time.time() - CACHE_FILE.stat().st_mtime
        if age < CACHE_MAX_AGE:
            needs_fetch = False

    if needs_fetch:
        try:
            resp = requests.get(
                "https://taotensorlaw.com/price_data.json",
                timeout=30,
                headers={"User-Agent": "TAO-TensorLaw-Dashboard/1.0"},
            )
            resp.raise_for_status()
            data = resp.json()
            CACHE_FILE.write_text(json.dumps(data))
            print(f"[{datetime.now()}] Fetched fresh price_data.json ({len(data)} points)")
        except Exception as e:
            print(f"[{datetime.now()}] Failed to fetch price_data.json: {e}")

    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return []


def fetch_binance_price():
    """Get current TAO/USDT price from Binance public API."""
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": "TAOUSDT"},
            timeout=10,
        )
        resp.raise_for_status()
        return float(resp.json()["price"])
    except Exception as e:
        print(f"[{datetime.now()}] Binance API error: {e}")
        return None


def fetch_binance_daily_klines(start_ts_ms):
    """Fetch daily klines from Binance to fill gap between cached data and now."""
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={
                "symbol": "TAOUSDT",
                "interval": "1d",
                "startTime": int(start_ts_ms),
                "limit": 1000,
            },
            timeout=15,
        )
        resp.raise_for_status()
        klines = resp.json()
        # Each kline: [open_time, open, high, low, close, ...]
        # Return as [[timestamp_seconds, close_price], ...]
        result = []
        for k in klines:
            ts_s = k[0] / 1000  # open_time ms -> s
            close = float(k[4])
            result.append([ts_s, close])
        return result
    except Exception as e:
        print(f"[{datetime.now()}] Binance klines error: {e}")
        return []


def linear_regression_log10(data):
    """Log-log linear regression on (day_number, price) pairs.
    data: list of dicts with 'x' (day+offset) and 'y' (price)
    Returns slope, intercept in log10 space.
    """
    valid = [p for p in data if p["x"] > 0 and p["y"] > 0]
    n = len(valid)
    if n < 2:
        return 0, 0

    sum_x = sum_y = sum_xy = sum_x2 = 0
    for p in valid:
        lx = math.log10(p["x"])
        ly = math.log10(p["y"])
        sum_x += lx
        sum_y += ly
        sum_xy += lx * ly
        sum_x2 += lx * lx

    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return 0, 0

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept


def calculate_r_squared(data, slope, intercept):
    """R² goodness of fit in log10 space."""
    valid = [p for p in data if p["x"] > 0 and p["y"] > 0]
    if len(valid) < 2:
        return 0

    y_mean = sum(math.log10(p["y"]) for p in valid) / len(valid)
    ss_tot = ss_res = 0
    for p in valid:
        ly = math.log10(p["y"])
        lx = math.log10(p["x"])
        predicted = slope * lx + intercept
        ss_tot += (ly - y_mean) ** 2
        ss_res += (ly - predicted) ** 2

    if ss_tot == 0:
        return 1
    return max(0, 1 - ss_res / ss_tot)


def calculate_residuals(data, slope, intercept):
    """Sorted residuals: log10(actual) - log10(predicted)."""
    valid = [p for p in data if p["x"] > 0 and p["y"] > 0]
    residuals = []
    for p in valid:
        lx = math.log10(p["x"])
        ly = math.log10(p["y"])
        predicted = slope * lx + intercept
        residuals.append(ly - predicted)
    residuals.sort()
    return residuals


def get_percentile(sorted_residuals, pct):
    """Interpolated percentile from sorted array."""
    if not sorted_residuals:
        return 0
    idx = (pct / 100) * (len(sorted_residuals) - 1)
    lower = int(math.floor(idx))
    upper = int(math.ceil(idx))
    if lower == upper:
        return sorted_residuals[lower]
    weight = idx - lower
    return sorted_residuals[lower] * (1 - weight) + sorted_residuals[upper] * weight


def compute_model(raw_data, day_offset=OFFSET_NAKAMOTO):
    """Run the full power law model on raw price data.
    raw_data: [[timestamp_s, price], ...]
    Returns dict with all model parameters.
    """
    if not raw_data or len(raw_data) < 2:
        return None

    # Build base data: day_index starts at 1
    base = []
    for i, point in enumerate(raw_data):
        ts, price = point[0], point[1]
        if price > 0:
            base.append({"time": ts * 1000, "dayIndex": i + 1, "y": price})

    if len(base) < 2:
        return None

    # Apply offset
    price_data = [{"x": p["dayIndex"] + day_offset, "y": p["y"]} for p in base]
    price_data = [p for p in price_data if p["x"] > 0]

    slope, intercept = linear_regression_log10(price_data)
    r2 = calculate_r_squared(price_data, slope, intercept)
    residuals = calculate_residuals(price_data, slope, intercept)

    p20 = get_percentile(residuals, 20)
    p50 = get_percentile(residuals, 50)
    p80 = get_percentile(residuals, 80)
    p1 = get_percentile(residuals, 1)
    p99 = get_percentile(residuals, 99)

    # Current zone based on last data point
    last = base[-1]
    last_x = last["dayIndex"] + day_offset
    if last_x > 0:
        last_log_x = math.log10(last_x)
        last_log_y = math.log10(last["y"])
        predicted = slope * last_log_x + intercept
        last_residual = last_log_y - predicted
    else:
        last_residual = 0

    if last_residual > p80:
        zone = "Bubble"
    elif last_residual > p50:
        zone = "Expensive"
    elif last_residual > p20:
        zone = "Value"
    else:
        zone = "Discount"

    # "Today" band levels
    start_ts = base[0]["time"]
    ms_per_day = 86400000
    now_ms = time.time() * 1000
    days_since_start = round((now_ms - start_ts) / ms_per_day)
    today_index = base[0]["dayIndex"] + days_since_start
    today_log_x = math.log10(today_index + day_offset) if (today_index + day_offset) > 0 else 0

    median_intercept = intercept + p50

    def band_price(pctl_intercept):
        return 10 ** (slope * today_log_x + pctl_intercept)

    bands = {
        "bubble": [band_price(intercept + p80), band_price(intercept + p99)],
        "expensive": [band_price(intercept + p50), band_price(intercept + p80)],
        "value": [band_price(intercept + p20), band_price(intercept + p50)],
        "discount": [band_price(intercept + p1), band_price(intercept + p20)],
    }

    # Fair value projections
    first_date = datetime.fromtimestamp(base[0]["time"] / 1000)
    first_day_index = base[0]["dayIndex"]
    current_year = datetime.now().year

    projections = []
    target_dates = [
        ("Today", datetime.now()),
        (str(current_year + 1), datetime(current_year + 1, 1, 1)),
        (str(current_year + 2), datetime(current_year + 2, 1, 1)),
        (str(current_year + 3), datetime(current_year + 3, 1, 1)),
    ]
    for label, dt in target_dates:
        diff_days = (dt - first_date).days
        target_day = first_day_index + diff_days
        day_for_log = target_day + day_offset
        if day_for_log > 0:
            proj_price = 10 ** (slope * math.log10(day_for_log) + median_intercept)
            projections.append({"label": label, "price": round(proj_price, 2)})
        else:
            projections.append({"label": label, "price": None})

    # Trend line data for chart (extend 365 days forward)
    last_day = base[-1]["dayIndex"]
    future_day = last_day + 365
    trend_line = []
    # Generate 200 points from first to future
    first_x = base[0]["dayIndex"] + day_offset
    last_x_extended = future_day + day_offset
    if first_x > 0 and last_x_extended > first_x:
        log_min = math.log10(first_x)
        log_max = math.log10(last_x_extended)
        steps = 200
        for i in range(steps + 1):
            log_x = log_min + (log_max - log_min) * i / steps
            x_val = 10 ** log_x
            day_idx = x_val - day_offset
            ts_ms = base[0]["time"] + (day_idx - base[0]["dayIndex"]) * ms_per_day
            # Median (fair value) trend
            y_median = 10 ** (slope * log_x + median_intercept)
            # P1 and P99 bounds
            y_p1 = 10 ** (slope * log_x + intercept + p1)
            y_p99 = 10 ** (slope * log_x + intercept + p99)
            y_p20 = 10 ** (slope * log_x + intercept + p20)
            y_p80 = 10 ** (slope * log_x + intercept + p80)
            trend_line.append({
                "timestamp": ts_ms / 1000,
                "median": round(y_median, 4),
                "p1": round(y_p1, 4),
                "p99": round(y_p99, 4),
                "p20": round(y_p20, 4),
                "p80": round(y_p80, 4),
            })

    return {
        "slope": round(slope, 6),
        "intercept": round(intercept, 6),
        "median_intercept": round(median_intercept, 6),
        "r2": round(r2, 6),
        "day_offset": day_offset,
        "zone": zone,
        "last_price": base[-1]["y"],
        "last_date": datetime.fromtimestamp(base[-1]["time"] / 1000).isoformat(),
        "data_points": len(base),
        "bands": bands,
        "projections": projections,
        "price_history": [[p["time"] / 1000, p["y"]] for p in base],
        "trend_line": trend_line,
    }


def refresh_data_periodically():
    """Background thread to refresh data every 24h."""
    while True:
        time.sleep(CACHE_MAX_AGE)
        try:
            fetch_price_data()
        except Exception as e:
            print(f"Background refresh error: {e}")


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/data")
def api_data():
    raw = fetch_price_data()
    if not raw:
        return jsonify({"error": "No data available"}), 500

    # Supplement with Binance daily klines to fill the gap
    last_ts = raw[-1][0]  # seconds
    now_ts = time.time()
    gap_days = (now_ts - last_ts) / 86400

    if gap_days > 1:
        start_ms = (last_ts + 86400) * 1000  # next day
        klines = fetch_binance_daily_klines(start_ms)
        if klines:
            # Avoid duplicates: only add klines with timestamp > last cached
            for k in klines:
                if k[0] > last_ts + 43200:  # at least 12h after last point
                    raw.append(k)

    # Sort by timestamp and deduplicate
    raw.sort(key=lambda x: x[0])
    seen = set()
    deduped = []
    for point in raw:
        ts = point[0]
        day_key = int(ts // 86400)  # bucket by day
        if day_key not in seen:
            seen.add(day_key)
            deduped.append(point)
    raw = deduped

    # Support custom day offset via query param
    offset = request.args.get("offset", OFFSET_NAKAMOTO, type=int)

    model = compute_model(raw, day_offset=offset)
    if not model:
        return jsonify({"error": "Model computation failed"}), 500

    # Add live price
    live_price = fetch_binance_price()
    model["live_price"] = live_price

    return jsonify(model)


if __name__ == "__main__":
    # Start background data refresh thread
    t = threading.Thread(target=refresh_data_periodically, daemon=True)
    t.start()

    # Initial data fetch
    fetch_price_data()

    print("Starting TAO Tensor Law Dashboard on http://localhost:8086")
    port = int(os.environ.get("PORT", 8086))
    app.run(host="0.0.0.0", port=port, debug=False)
