"""Power-law model computation for TAO price data."""

import math
import time
from datetime import datetime

OFFSET_NAKAMOTO = 486


def sort_dedupe(data):
    """Sort by timestamp, deduplicate by day bucket."""
    data.sort(key=lambda x: x[0])
    seen = set()
    out = []
    for point in data:
        day_key = int(point[0] // 86400)
        if day_key not in seen:
            seen.add(day_key)
            out.append(point)
    return out


def gap_fill_and_update(raw, live_price):
    """Append missing Binance klines and update today's price in-place.
    Returns (updated_raw, live_price).

    Caller is responsible for fetching klines if gap > 1 day before calling this.
    This function handles replacing/appending today's live price.
    """
    if not raw or not live_price:
        return raw

    now_ts = time.time()
    last_ts = raw[-1][0]

    if int(now_ts // 86400) == int(last_ts // 86400):
        # Last point is from today — update its price
        raw[-1][1] = live_price
    else:
        # Last point is from a previous day — append today
        raw.append([now_ts, live_price])

    return raw


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

    # Trend line aligned to price history timestamps + future extension
    trend_line = []
    last_day = base[-1]["dayIndex"]
    last_ts_ms = base[-1]["time"]

    # Build from actual price history points
    for p in base:
        day_idx = p["dayIndex"]
        x_val = day_idx + day_offset
        if x_val <= 0:
            continue
        log_x = math.log10(x_val)
        y_median = 10 ** (slope * log_x + median_intercept)
        y_p1 = 10 ** (slope * log_x + intercept + p1)
        y_p99 = 10 ** (slope * log_x + intercept + p99)
        y_p20 = 10 ** (slope * log_x + intercept + p20)
        y_p80 = 10 ** (slope * log_x + intercept + p80)
        trend_line.append({
            "timestamp": p["time"] / 1000,
            "median": round(y_median, 4),
            "p1": round(y_p1, 4),
            "p99": round(y_p99, 4),
            "p20": round(y_p20, 4),
            "p80": round(y_p80, 4),
        })

    # Extend 365 days into the future (monthly points)
    for extra_days in range(30, 366, 30):
        day_idx = last_day + extra_days
        x_val = day_idx + day_offset
        if x_val <= 0:
            continue
        log_x = math.log10(x_val)
        ts_ms = last_ts_ms + extra_days * ms_per_day
        y_median = 10 ** (slope * log_x + median_intercept)
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
