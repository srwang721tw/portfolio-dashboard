# Portfolio Dashboard

A personal Taiwan + US stock portfolio tracker built with Streamlit and backed by Neon PostgreSQL, deployed on Railway.

## Tech Stack & Highlights

| Layer | Choice | Notes |
|---|---|---|
| **Frontend** | [Streamlit](https://streamlit.io) | Single-page, tab-based UI; dark-mode CSS overrides |
| **Charts** | [Altair](https://altair-viz.github.io) | Pie, line+point, bar charts with custom dark theme |
| **Database** | [Neon PostgreSQL](https://neon.tech) (free tier) | Serverless Postgres; auto-suspends after 5-min idle |
| **ORM / driver** | `psycopg2-binary` + `ThreadedConnectionPool` | Thread-safe for Streamlit's multi-threaded runtime |
| **Prices** | [yfinance](https://github.com/ranaroussi/yfinance) | Taiwan ETFs (`.TW` suffix) + US stocks + `USDTWD=X` |
| **FX rate** | Cathay Bank digital-channel scrape → yfinance fallback | Live buying rate from Cathay's billboard page |
| **Auth** | PBKDF2-SHA256 (200k iterations) via `hashlib` | Password-only; no 2FA required |
| **Deployment** | [Railway](https://railway.app) | `railway.toml` configures start command; auto-deploys on push |

### Key design highlights

- **Per-user data isolation** — every DB table has a `username` FK; users can only ever see their own data
- **Neon cold-start resilience** — `_with_conn()` catches `OperationalError` / `InterfaceError`, rebuilds the connection pool, and retries once automatically
- **TW cost basis from raw transactions** — 對帳單 rows are stored as-is; net holdings are re-derived at read time via `_aggregate_tw_transactions()`, preserving full history
- **US cost basis in TWD** — entered as a fixed TWD amount (what you actually wired to your broker) to eliminate FX-drift noise from P&L charts
- **TW sell-cost factor** — market values are discounted by `≈ 0.99860` (brokerage commission + ETF transaction tax) so displayed values reflect net liquidation proceeds
- **Multi-file CSV upload** — upload multiple CSVs at once; TW rows are merged and deduped, US holdings use last-file-wins per symbol
- **Zero-seeding deploys** — no Google Drive file seeding needed; `db.ensure_schema()` runs idempotently on every startup

## Features

- **Live prices** — Taiwan ETFs via yfinance `.TW` and US stocks, refreshed on demand (5-min cache)
- **Portfolio Holdings table** — cost basis, current value (sell-cost adjusted for TW), unrealized P&L and return per holding; TW / US / Grand Total subtotals
- **Allocation charts** — holdings breakdown pie + TW vs US split pie
- **P&L Change History** — daily (30d) line + point chart, daily bar chart, monthly bar chart (this year), annual bar chart (3 years)
- **Pledge Monitoring** — pledged stock value, total loan + accrued interest, overall maintenance ratio gauge, per-loan monitoring table, inline loan editor
- **Multi-user accounts** — register / sign in; per-user data isolation; username and password changes supported
- **CSV upload** — multi-file upload with format validation, size/row-count limits, and automatic deduplication

## Supported CSV Formats

| Market | Format | Detection |
|---|---|---|
| TW | 國泰證券 對帳單 (transaction history) | Columns: `股名`, `日期`, `成交股數`, `淨收付` |
| US | 複委託庫存 (holdings snapshot) | Columns: `代號`, `目前庫存`, `均價` |

Upload from the **Upload** tab. Multiple files can be uploaded at once.

## Project Structure

```
portfolio-dashboard/
├── app.py                   # Streamlit entry point; all UI sections
├── config/
│   └── settings.py          # Constants: tickers, thresholds, colors, TTLs
├── utils/
│   ├── auth.py              # Password hashing, user CRUD, session helpers
│   ├── data_loader.py       # CSV parsing (對帳單 / 複委託庫存), DB read/write wrappers
│   ├── db.py                # Neon PostgreSQL layer: schema, connection pool, all CRUD
│   ├── history_manager.py   # Daily portfolio snapshot save/load
│   ├── portfolio_calc.py    # Holdings enrichment, P&L, pledge ratio calculations
│   └── price_fetcher.py     # yfinance wrappers for live + historical prices
├── data/                    # Local sample CSVs only (gitignored for real data)
│   ├── sample_tw_stocks.csv
│   └── sample_us_stocks.csv
├── setup_db.py              # One-time: create DB schema (run before first deploy)
├── migrate_to_neon.py       # One-time: migrate flat-file data into Neon
├── requirements.txt
└── railway.toml
```

## Local Setup

```bash
# 1. Clone and create virtual environment
git clone https://github.com/srwang721tw/portfolio-dashboard.git
cd portfolio-dashboard
python -m venv .venv && source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment — copy and fill in DATABASE_URL + APP_SECRET_KEY
cp .env.example .env

# 4. Create DB schema (only needed once)
python setup_db.py

# 5. Run
streamlit run app.py
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | **Yes** | Neon PostgreSQL connection string (`postgresql://...?sslmode=require`) |
| `APP_SECRET_KEY` | Yes | Random hex string for session security |

> **Never commit `.env` or `DATABASE_URL` to git.** The `.env` file is gitignored.

## Deploy to Railway

1. Push this repo to GitHub
2. Create a new Railway project from the GitHub repo
3. Add environment variables in the Railway dashboard:
   - `DATABASE_URL` — paste the Neon connection string
   - `APP_SECRET_KEY` — any random hex string
4. Railway auto-deploys on every push; `railway.toml` handles the start command

The app calls `db.ensure_schema()` on every startup — no manual schema migration needed.

## TW Stock Sell-Cost Factor

Values in the **Portfolio Holdings** table for TW stocks are adjusted by:

```
factor = 1 - ((0.1425 × 0.28 + 0.1) / 100)   # ≈ 0.99860
```

This accounts for the sell-side brokerage commission (0.1425% at 28% discount) and ETF transaction tax (0.1%), showing net liquidation proceeds rather than raw market value.

## Pledge Maintenance Ratio

```
Maintenance Ratio = Pledged Stock Value / (Loan Principal + Accrued Interest) × 100%
```

| Zone | Threshold |
|---|---|
| 🔴 Margin Call | < 140% |
| 🟠 Warning | 140% – 200% |
| 🟡 Watch | 200% – 300% |
| 🟢 Safe | ≥ 300% |

Interest can be entered manually per loan (overrides the auto-computed rate × days formula).
