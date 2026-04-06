"""TAO Tensor Law API - Vercel Serverless Function

Note: Binance API blocks cloud provider IPs (AWS/Vercel), so gap-filling
and live price are handled client-side in the frontend instead.
"""

import json
import sys
from pathlib import Path

from flask import Flask, jsonify, request

# Allow imports from project root (for lib/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.model import OFFSET_NAKAMOTO, compute_model
from lib.fetcher import fetch_from_upstream

FALLBACK_FILE = PROJECT_ROOT / "price_data.json"

app = Flask(__name__)


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.route("/api/data")
def api_data():
    raw = fetch_from_upstream(timeout=5)
    if not raw and FALLBACK_FILE.exists():
        raw = json.loads(FALLBACK_FILE.read_text())
    if not raw:
        return jsonify({"error": "No data available"}), 500

    offset = request.args.get("offset", OFFSET_NAKAMOTO, type=int)

    model = compute_model(raw, day_offset=offset)
    if not model:
        return jsonify({"error": "Model computation failed"}), 500

    model["last_ts"] = raw[-1][0]

    return jsonify(model)
