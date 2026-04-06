"""External API calls for TAO price data."""

from datetime import datetime

import requests


def fetch_from_upstream():
    """Fetch full price history from taotensorlaw.com."""
    try:
        resp = requests.get(
            "https://taotensorlaw.com/price_data.json",
            timeout=30,
            headers={"User-Agent": "TAO-TensorLaw-Dashboard/1.0"},
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[{datetime.now()}] Failed to fetch from upstream: {e}")
        return []


def fetch_binance_price():
    """Get current TAO/USDT spot price from Binance."""
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": "TAOUSDT"},
            timeout=10,
        )
        resp.raise_for_status()
        return float(resp.json()["price"])
    except Exception as e:
        print(f"[{datetime.now()}] Binance price API error: {e}")
        return None


def fetch_binance_daily_klines(start_ts_ms):
    """Fetch daily klines from Binance starting at given timestamp (ms).
    Returns [[timestamp_seconds, close_price], ...]
    """
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
        result = []
        for k in klines:
            ts_s = k[0] / 1000  # open_time ms -> s
            close = float(k[4])
            result.append([ts_s, close])
        return result
    except Exception as e:
        print(f"[{datetime.now()}] Binance klines error: {e}")
        return []
