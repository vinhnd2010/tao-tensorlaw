#!/usr/bin/env python3
"""TAO Tensor Law Dashboard - Self-hosted on port 8086"""

import json
import os
import time
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from lib.model import OFFSET_NAKAMOTO, sort_dedupe, gap_fill_and_update, compute_model
from lib.fetcher import fetch_from_upstream, fetch_binance_price, fetch_binance_daily_klines

app = Flask(__name__, template_folder="public")

DATA_DIR = Path(__file__).parent
CACHE_FILE = DATA_DIR / "price_data.json"
APPEND_INTERVAL = 3600  # append new Binance data every 1 hour

# In-memory timestamp of last Binance append (avoid hammering API)
_last_append_time = 0


def bootstrap_from_upstream():
    """One-time bootstrap: fetch full history from taotensorlaw.com.
    Only called when price_data.json does not exist yet."""
    print(f"[{datetime.now()}] Bootstrapping from taotensorlaw.com ...")
    data = fetch_from_upstream()
    if data:
        data = sort_dedupe(data)
        CACHE_FILE.write_text(json.dumps(data))
        print(f"[{datetime.now()}] Bootstrap done: {len(data)} points saved.")
    return data


def append_binance_data(data):
    """Append any new daily klines from Binance since last data point.
    Mutates and saves data in place. Returns updated data."""
    global _last_append_time
    now = time.time()
    if now - _last_append_time < APPEND_INTERVAL:
        return data  # too soon, skip

    last_ts = data[-1][0]
    gap_days = (now - last_ts) / 86400
    if gap_days < 1:
        return data  # nothing new yet

    start_ms = (last_ts + 86400) * 1000
    klines = fetch_binance_daily_klines(start_ms)
    if klines:
        added = 0
        for k in klines:
            if k[0] > last_ts + 43200:  # at least 12h gap
                data.append(k)
                added += 1
        if added:
            data = sort_dedupe(data)
            CACHE_FILE.write_text(json.dumps(data))
            print(f"[{datetime.now()}] Appended {added} new Binance points. Total: {len(data)}")

    _last_append_time = now
    return data


def fetch_price_data():
    """Load local price_data.json, bootstrapping from upstream if it doesn't exist yet.
    Then append any new Binance data since the last stored point."""
    if not CACHE_FILE.exists():
        data = bootstrap_from_upstream()
    else:
        data = json.loads(CACHE_FILE.read_text())

    if not data:
        return []

    # Append latest Binance data (throttled to once per hour)
    data = append_binance_data(data)
    return data


def build_enriched_data():
    """Alias kept for /api/refresh endpoint — forces a Binance append."""
    global _last_append_time
    _last_append_time = 0  # reset throttle
    return fetch_price_data()


def refresh_data_periodically():
    """Background thread: append new Binance data every hour."""
    while True:
        time.sleep(APPEND_INTERVAL)
        try:
            fetch_price_data()
        except Exception as e:
            print(f"Background append error: {e}")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def api_data():
    raw = fetch_price_data()
    if not raw:
        return jsonify({"error": "No data available"}), 500

    # Update today's price with latest from Binance
    live_price = fetch_binance_price()
    if live_price:
        gap_fill_and_update(raw, live_price)

    # Support custom day offset via query param
    offset = request.args.get("offset", OFFSET_NAKAMOTO, type=int)

    model = compute_model(raw, day_offset=offset)
    if not model:
        return jsonify({"error": "Model computation failed"}), 500

    model["live_price"] = live_price
    model["last_ts"] = raw[-1][0]  # unix seconds — frontend uses this as past/future cutoff

    return jsonify(model)


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Force rebuild enriched price_data.json from upstream + Binance."""
    data = build_enriched_data()
    return jsonify({"ok": True, "points": len(data)})


if __name__ == "__main__":
    # Start background data refresh thread
    t = threading.Thread(target=refresh_data_periodically, daemon=True)
    t.start()

    # Bootstrap or load existing data on startup
    fetch_price_data()

    print("Starting TAO Tensor Law Dashboard on http://localhost:8086")
    port = int(os.environ.get("PORT", 8086))
    app.run(host="0.0.0.0", port=port, debug=False)
