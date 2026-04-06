"""TAO Tensor Law API - Vercel Serverless Function"""

import sys
import time
from pathlib import Path

from flask import Flask, jsonify, request

# Allow imports from project root (for lib/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.model import OFFSET_NAKAMOTO, gap_fill_and_update, compute_model
from lib.fetcher import fetch_from_upstream, fetch_binance_price, fetch_binance_daily_klines

app = Flask(__name__)


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.route("/api/data")
def api_data():
    raw = fetch_from_upstream()
    if not raw:
        return jsonify({"error": "No data available"}), 500

    # Gap-fill missing days from Binance
    last_ts = raw[-1][0]
    now_ts = time.time()
    last_day = int(last_ts // 86400)
    today = int(now_ts // 86400)

    if today > last_day:
        start_ms = (last_ts + 86400) * 1000
        klines = fetch_binance_daily_klines(start_ms)
        if klines:
            for k in klines:
                if k[0] > last_ts + 43200:
                    raw.append(k)

    # Update today's price with latest from Binance
    live_price = fetch_binance_price()
    if live_price:
        gap_fill_and_update(raw, live_price)

    offset = request.args.get("offset", OFFSET_NAKAMOTO, type=int)

    model = compute_model(raw, day_offset=offset)
    if not model:
        return jsonify({"error": "Model computation failed"}), 500

    model["live_price"] = live_price
    model["last_ts"] = raw[-1][0]

    return jsonify(model)
