# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (activate venv first)
source .venv/bin/activate
pip install -r requirements.txt

# Run locally
streamlit run app.py

# One-time Drive setup (run before first Railway deploy)
python setup_drive.py
```

No test suite exists; verify changes by running the app.

## Architecture

Single-page Streamlit app (`app.py`) with a tab-based UI. All business logic is in `utils/`; `config/settings.py` holds constants, file paths, and the Plotly/Altair theme.

**Data flow:**
1. On login, `utils/gdrive.py::sync_down_all()` pulls all data files from Google Drive (single source of truth) into `data/` (local cache, gitignored).
2. `utils/data_loader.py` reads the local CSVs and JSON files; writes go back to local + Drive.
3. `utils/price_fetcher.py` fetches live and historical prices via yfinance, cached with `@st.cache_data`.
4. `utils/portfolio_calc.py` enriches holdings with prices, computes P&L and pledge ratios.
5. `app.py` renders everything using Streamlit widgets and Plotly/Altair charts.

**Google Drive limitation:** Service accounts cannot *create* files on personal Drive (no storage quota). They can only *update* existing files. Run `setup_drive.py` once locally to seed placeholder files before the first deploy. `gdrive.upload()` silently returns `False` if the target file isn't already in Drive.

## Key Design Decisions

**Adding or changing tracked tickers** requires two files:
- `config/settings.py`: `TW_TICKERS` / `US_TICKERS` (symbol → yfinance symbol mapping)
- `utils/data_loader.py`: `TW_NAME_TO_TICKER` (Chinese name → ticker) and `TW_INCLUDE_FROM` (ticker → optional start date for cost-basis cutoff)

**CSV format detection** (`data_loader.py`): Two Taiwan brokerage formats are auto-detected:
- 對帳單 (transaction history): detected by columns `{'股名', '日期', '成交股數', '淨收付'}` — net holdings and cost basis are computed from transaction history, not a snapshot.
- 複委託庫存 (US holdings snapshot): detected by `{'代號', '目前庫存', '均價'}`.
- Generic summary CSVs fall back to alias-based column matching.

**US cost basis in TWD** (`us_cost_twd`): Stored in `data/us_cost_config.json` and entered via the dashboard UI. When set, it's used as a fixed TWD cost (immune to FX drift) rather than computing `shares × USD_cost × FX_rate`. This prevents historical FX fluctuations from distorting the cost line in P&L charts.

**TW sell-cost factor**: TW holdings market values are adjusted by `1 - ((0.1425 × 0.28 + 0.1) / 100) ≈ 0.99860` to reflect net liquidation value (brokerage commission + ETF transaction tax).

**Authentication**: PBKDF2-SHA256 (200k iterations) + TOTP 2FA via `pyotp`. `users.json` is synced from Drive on every app start so accounts survive Railway redeploys.

**Price caching**: `PRICE_CACHE_TTL = 300s` (current prices), `HISTORY_CACHE_TTL = 3600s` (historical). USD/TWD rate primary source is Cathay Bank digital-channel scrape; yfinance is the fallback.

## Deployment

Railway reads `railway.toml`. Set three env vars in Railway dashboard:
- `APP_SECRET_KEY` — random hex, required
- `GOOGLE_SERVICE_ACCOUNT_JSON` — full JSON content of GCP service account key
- `GOOGLE_DRIVE_FOLDER_ID` — Drive folder ID shared with the service account (Editor role)

Without Drive env vars, the app works locally using only `data/` files and falls back to sample data if those are missing.
