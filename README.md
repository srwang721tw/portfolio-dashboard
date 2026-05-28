# Portfolio Dashboard

A personal Taiwan + US stock portfolio tracker built with Streamlit, deployed on Railway.

## Features

- **Live prices** — Taiwan ETFs (via yfinance `.TW`) and US stocks, refreshed on demand
- **Portfolio Holdings table** — cost basis, current value (with TW sell-cost factor applied), unrealized P&L, and return per holding
- **Allocation charts** — holdings breakdown pie chart + TW / US split
- **P&L Change History** — daily (30d), monthly (this year), and annual (3-year) bar charts
- **Pledge Monitoring** — pledged stock value, total loan + accrued interest, overall maintenance ratio, per-loan table
- **Google Drive sync** — holdings CSVs, pledge config, and user accounts persisted to Drive; survives Railway redeploys
- **Authentication** — PBKDF2-SHA256 password hashing + TOTP 2FA (Google Authenticator)

## Supported Data Formats

| File | Format |
|---|---|
| TW stocks | 國泰證券 對帳單 (transaction history CSV) |
| US stocks | 複委託庫存 (holdings snapshot CSV) |

Both can be uploaded directly from the **Upload** tab inside the dashboard.

## Project Structure

```
portfolio-dashboard/
├── app.py                  # Main Streamlit app
├── config/
│   └── settings.py         # Constants, file paths, color palette
├── utils/
│   ├── auth.py             # User accounts, password hashing, TOTP 2FA
│   ├── data_loader.py      # CSV parsing (對帳單 / 複委託庫存 formats)
│   ├── gdrive.py           # Google Drive upload / download via service account
│   ├── history_manager.py  # Daily portfolio snapshot storage
│   ├── portfolio_calc.py   # Holdings enrichment, P&L, pledge ratio calculations
│   └── price_fetcher.py    # yfinance wrappers for live + historical prices
├── data/                   # Local data files (gitignored)
│   ├── sample_tw_stocks.csv
│   └── sample_us_stocks.csv
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

# 3. Configure environment
cp .env.example .env
# Edit .env — add APP_SECRET_KEY and optionally GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_DRIVE_FOLDER_ID

# 4. Run
streamlit run app.py
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `APP_SECRET_KEY` | Yes | Random hex string for session security |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | No | Full JSON content of GCP service account key |
| `GOOGLE_DRIVE_FOLDER_ID` | No | Drive folder ID shared with the service account |

> **Never commit `GOOGLE_SERVICE_ACCOUNT_JSON` or `.env` to git.**

## Google Drive Persistence

When `GOOGLE_SERVICE_ACCOUNT_JSON` and `GOOGLE_DRIVE_FOLDER_ID` are set, the app automatically:

- Downloads `users.json`, `pledge_config.json`, `us_cost_config.json` on every login (so accounts survive redeploys)
- Uploads those files back to Drive whenever they are modified
- Falls back to sample data if no CSVs are found in Drive

Share your Drive folder with the service account email before first use.

## Deploy to Railway

1. Push this repo to GitHub
2. Create a new Railway project from the GitHub repo
3. Add environment variables in Railway's dashboard:
   - `APP_SECRET_KEY`
   - `GOOGLE_SERVICE_ACCOUNT_JSON` (paste the full JSON)
   - `GOOGLE_DRIVE_FOLDER_ID`
4. Railway auto-deploys on every push; the `railway.toml` handles the start command

## TW Stock Sell-Cost Factor

Values in the **Portfolio Holdings** table for TW stocks are adjusted by:

```
factor = 1 - ((0.1425 × 0.28 + 0.1) / 100)   # ≈ 0.99860
```

This accounts for the sell-side brokerage commission (0.1425% at 28% discount) and ETF transaction tax (0.1%), representing net liquidation proceeds rather than raw market value.

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
