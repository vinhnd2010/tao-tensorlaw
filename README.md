# TAO Tensor Law Dashboard

A power-law pricing model dashboard for the [Bittensor](https://bittensor.com/) TAO token. Fits a logarithmic regression to historical TAO/USDT price data and visualizes valuation zones, percentile bands, and fair value projections.

## How It Works

The dashboard applies a **power-law model** (log-log linear regression) to TAO's price history using the Nakamoto offset (486 days). It calculates:

- **Valuation zones** — Bubble, Expensive, Value, or Discount based on where the current price falls relative to percentile bands
- **Percentile bands** — 1st, 20th, 50th (fair value), 80th, and 99th percentile trend lines
- **Fair value projections** — Median model price for today and the next 3 years
- **R² goodness of fit** — How well the power-law model fits historical data

## Project Structure

```
tao-tensorlaw/
├── lib/                   # Shared Python modules
│   ├── __init__.py
│   ├── model.py           # Power-law computation (regression, residuals,
│   │                      #   percentiles, compute_model, gap_fill_and_update)
│   └── fetcher.py         # External API calls (taotensorlaw.com, Binance)
├── app.py                 # Flask server (local deployment)
│                          #   - Bootstraps & manages price_data.json
│                          #   - Background thread appends Binance data hourly
│                          #   - Serves /api/data (model computation)
│                          #   - Serves public/index.html
├── api/
│   └── data.py            # Vercel serverless function (thin wrapper)
│                          #   - Fetches data from taotensorlaw.com
│                          #   - Computes model via lib/model.py
│                          #   - No Binance calls (blocked from cloud IPs)
├── public/
│   └── index.html         # Single frontend (used by both Flask and Vercel)
│                          #   - Client-side Binance gap-fill for stale data
│                          #   - Live price updates from Binance
├── price_data.json        # Local cache of enriched price data
├── requirements.txt       # Python dependencies (Flask, requests, gunicorn)
├── Procfile               # Heroku deployment config
└── vercel.json            # Vercel deployment config
```

## Data Flow

### Local (Flask)

```
┌──────────────────┐       ┌────────────────┐       ┌──────────────┐
│ taotensorlaw.com │       │  Binance API   │       │  Binance API │
│ /price_data.json │       │  /api/v3/      │       │  /ticker/    │
│ (bootstrap only) │       │  klines        │       │  price       │
└────────┬─────────┘       └───────┬────────┘       └──────┬───────┘
         │                         │ hourly append          │
         │  one-time if            │ (background thread)    │
         │  file missing           │                        │
         ▼                         ▼                        │
       ┌───────────────────────────────┐                    │
       │      price_data.json          │                    │
       │      (local, persisted)       │                    │
       └──────────────┬────────────────┘                    │
                      │                                     │
             read + update today's                          │
             price + compute model                          │
                      │                                     │
                      ▼                                     │
              ┌────────────────┐                            │
              │   /api/data    │                            │
              │   (Flask)      │                            │
              └───────┬────────┘                            │
                      │ JSON response                       │
                      ▼                                     ▼
              ┌──────────────────────────────────────────────┐
              │             public/index.html                │
              │  - Renders chart (Chart.js)                  │
              │  - Auto-refreshes every 5 min                │
              │  - Live price updates every 30s              │
              └─────────────────────────────────────────────┘
```

### Vercel (Serverless)

```
┌──────────────────┐       ┌──────────────┐    ┌──────────────┐
│ taotensorlaw.com │       │  Binance API │    │  Binance API │
│ /price_data.json │       │  /api/v3/    │    │  /ticker/    │
│  (per request)   │       │  klines      │    │  price       │
└────────┬─────────┘       └──────┬───────┘    └──────┬───────┘
         │                        │                    │
         │                        │  ✗ blocked from    │  ✗ blocked from
         │                        │    cloud IPs       │    cloud IPs
         ▼                        │                    │
┌─────────────────┐               │                    │
│   /api/data     │               │                    │
│ (Vercel fn)     │               │                    │
│ model from      │               │                    │
│ upstream only   │               │                    │
└────────┬────────┘               │                    │
         │ JSON (possibly stale)  │                    │
         ▼                        ▼                    ▼
┌──────────────────────────────────────────────────────────┐
│                   public/index.html                      │
│  1. Receive model from /api/data                         │
│  2. If data stale: fetch Binance klines (browser → OK)   │
│  3. Append missing days to price_history                  │
│  4. Fetch live price, update today's entry                │
│  5. Render chart with complete data                       │
└──────────────────────────────────────────────────────────┘
```

> **Why client-side gap-fill on Vercel?** Binance blocks API requests from cloud provider IPs (AWS/Vercel). The browser can reach Binance directly, so the frontend handles gap-filling and live price updates when the server can't.

### Local vs Vercel Comparison

| | Local (app.py) | Vercel (api/data.py) |
|---|---|---|
| **Data source** | Local `price_data.json` file | Fetches from taotensorlaw.com per request |
| **Bootstrap** | Downloads from taotensorlaw.com if file missing | N/A (stateless) |
| **Binance gap-fill** | Server-side, background thread every 1h, persisted | Client-side (browser), per page load |
| **Live price** | Server-side (updates model) + client-side (display) | Client-side only (browser → Binance) |
| **Filesystem** | Read/write (`price_data.json` updated in place) | Read-only (serverless, no persistent storage) |
| **Model computation** | Server-side with current data | Server-side with upstream data (may be ~1 day stale) |
| **Shared code** | `lib/model.py`, `lib/fetcher.py` | Same shared modules |

## Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Start the server (http://localhost:8086)
python app.py
```

On first run, `price_data.json` is bootstrapped from taotensorlaw.com. After that, the background thread appends new daily klines from Binance every hour.

## Deployment

### Vercel

Push to the repo — Vercel deploys automatically using `vercel.json`:
- `public/index.html` is served as static
- `api/data.py` handles `/api/data` as a serverless function
- Frontend handles Binance gap-fill and live price client-side

### Heroku

```bash
heroku create
git push heroku main
```

Uses `Procfile`: `web: gunicorn app:app --bind 0.0.0.0:$PORT`

## Tech Stack

- **Backend**: Python, Flask, requests
- **Shared lib**: `lib/model.py` (power-law math), `lib/fetcher.py` (API calls)
- **Frontend**: Vanilla JS, Chart.js, chartjs-adapter-date-fns
- **Data**: Binance API (TAO/USDT), taotensorlaw.com (historical bootstrap)
