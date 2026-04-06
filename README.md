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
├── app.py                 # Flask server (local deployment)
│                          #   - Bootstraps & manages price_data.json
│                          #   - Background thread appends Binance data hourly
│                          #   - Serves /api/data (model computation)
│                          #   - Serves public/index.html
├── api/
│   └── data.py            # Vercel serverless function
│                          #   - Same /api/data endpoint for Vercel deployment
│                          #   - Fetches data from taotensorlaw.com (stateless)
├── public/
│   └── index.html         # Single frontend (used by both Flask and Vercel)
├── gh-pages/
│   └── index.html         # GitHub Pages static version (standalone)
├── price_data.json        # Local cache of enriched price data (git-ignored at runtime)
├── requirements.txt       # Python dependencies (Flask, requests, gunicorn)
├── Procfile               # Heroku deployment config
└── vercel.json            # Vercel deployment config
```

## Data Flow

```
                    ┌─────────────────────────────┐
                    │   taotensorlaw.com           │
                    │   /price_data.json           │
                    │   (historical TAO prices)    │
                    └──────────────┬───────────────┘
                                   │
                         Bootstrap (one-time,
                         if local file missing)
                                   │
                                   ▼
┌──────────────┐          ┌────────────────┐         ┌──────────────┐
│  Binance API │──hourly──│ price_data.json │         │  Binance API │
│  /api/v3/    │  append  │  (local cache)  │         │  /ticker/    │
│  klines      │          └───────┬─────────┘         │  price       │
└──────────────┘                  │                    └──────┬───────┘
                                  │                           │
                         Read + compute model         Live price (display only)
                                  │                           │
                                  ▼                           │
                         ┌────────────────┐                   │
                         │   /api/data     │                   │
                         │  (Flask or      │                   │
                         │   Vercel fn)    │                   │
                         └───────┬────────┘                   │
                                 │                            │
                            JSON response                     │
                                 │                            │
                                 ▼                            ▼
                         ┌──────────────────────────────────────┐
                         │         public/index.html            │
                         │  - Renders chart (Chart.js)          │
                         │  - Shows zones, bands, projections   │
                         │  - Auto-refreshes every 5 min        │
                         │  - Live price updates every 30s      │
                         └──────────────────────────────────────┘
```

### Local (Flask) vs Vercel

| | Local (app.py) | Vercel (api/data.py) |
|---|---|---|
| **Data source** | Local `price_data.json` file | Fetches from taotensorlaw.com per request |
| **Bootstrap** | Downloads from taotensorlaw.com if file missing | N/A (stateless) |
| **Binance append** | Background thread every 1 hour, persisted to disk | Per-request in-memory gap-fill (not persisted) |
| **Filesystem** | Read/write (`price_data.json` updated in place) | Read-only (serverless, no persistent storage) |
| **Model computation** | Server-side (Python) | Server-side (Python serverless fn) |
| **Frontend** | `public/index.html` via Flask templates | `public/index.html` via static hosting |

> **Note on Vercel data freshness:** Vercel functions are stateless and short-lived — there is no background thread or persistent filesystem. Instead, on each request `api/data.py` fetches the base dataset from taotensorlaw.com, then if the data is >1 day stale, it gap-fills missing days from Binance's klines API in-memory before computing the model. This means data is always current at request time, but the gap-fill work is repeated on every request rather than persisted.

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

### Heroku

```bash
heroku create
git push heroku main
```

Uses `Procfile`: `web: gunicorn app:app --bind 0.0.0.0:$PORT`

## Tech Stack

- **Backend**: Python, Flask, requests
- **Frontend**: Vanilla JS, Chart.js, chartjs-adapter-date-fns
- **Data**: Binance API (TAO/USDT), taotensorlaw.com (historical bootstrap)
