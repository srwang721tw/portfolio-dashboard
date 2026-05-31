# CLAUDE.md

Developer reference for Claude Code. Covers architecture, design decisions, and all implementation details needed to make changes safely.

## Commands

```bash
# Activate virtual environment first
source .venv/bin/activate
pip install -r requirements.txt

# Run locally
streamlit run app.py

# Create DB schema (one-time, idempotent)
python setup_db.py

# Migrate flat-file data to Neon (one-time historical migration)
python migrate_to_neon.py
```

No test suite exists. Verify changes by running the app locally.

---

## Architecture

Single-page Streamlit app (`app.py`) with a tab-based UI. All business logic lives in `utils/`; constants and theme settings live in `config/settings.py`.

### Data flow

```
Login
  └─ db.ensure_schema()          idempotent on every startup
  └─ db.has_user_data(username)  determines which tabs to show

Dashboard tab
  └─ data_loader.load_tw_holdings(username)
       └─ db.get_tw_transactions()  → _aggregate_tw_transactions()
  └─ data_loader.load_us_holdings(username)
       └─ db.get_us_holdings()
  └─ price_fetcher.fetch_current_prices()   cached 300s
  └─ price_fetcher.fetch_usd_twd_rate()     cached 300s
  └─ portfolio_calc.enrich_holdings()
  └─ portfolio_calc.portfolio_summary()
  └─ history_manager.save_snapshot(username, ...)
       └─ db.upsert_history_snapshot()      rolling 730-day window

P&L History tab
  └─ price_fetcher.fetch_historical_prices()  cached 3600s
  └─ portfolio_calc.compute_portfolio_history()

Upload tab
  └─ validate_csv_upload()         size, row count, parsability
  └─ _parse_dazhangdan_rows()      TW: raw transaction rows
  └─ db.replace_tw_transactions()  atomic DELETE + bulk INSERT
  └─ _parse_fuzhuotuo()            US: holdings snapshot
  └─ db.replace_us_holdings()      atomic DELETE + INSERT
  └─ save_us_cost_twd()            auto-computed on US upload
```

---

## Database Schema (`utils/db.py`)

Neon PostgreSQL free tier. All tables use `username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE` for per-user isolation.

### Tables

| Table | Purpose |
|---|---|
| `users` | Accounts: `username` (PK), `password_hash`, `salt`, `totp_secret`, `totp_enabled`, `email`, `created_at` |
| `tw_transactions` | Raw 對帳單 rows: `symbol`, `name`, `trade_date`, `share_delta`, `cost_flow`, `uploaded_at` |
| `us_holdings` | US snapshot: `symbol` (UNIQUE per user), `name`, `shares`, `cost_per_share`, `currency` |
| `pledge_loans` | One row per loan: `loan_seq` (UNIQUE per user), `loan_amount_twd`, `interest_rate`, `start_date`, `expiry_date`, `override_interest_twd` |
| `pledge_stocks` | One row per pledged stock, FK to `pledge_loans.id` |
| `user_config` | Key-value store per user; currently only key `us_twd_cost` |
| `portfolio_history` | Daily snapshots: `date` (PK with username), `total_value_twd`, `total_pnl_twd`, `pnl_pct`; auto-trimmed to 730 days |

### Connection strategy

`ThreadedConnectionPool(minconn=1, maxconn=3)` — Streamlit spawns one thread per browser session; the pool is thread-safe. Neon free tier auto-suspends after 5 minutes of idle; `_with_conn()` catches `OperationalError` / `InterfaceError`, rebuilds the pool, and retries once automatically.

```python
def _with_conn(func):
    pool = _ensure_pool()
    try:
        return _attempt(pool)
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        # Neon cold-start: rebuild pool and retry once
        with _pool_lock:
            _pool.closeall()
            _pool = _build_pool()
        return _attempt(_pool)
```

### Schema migration

`ensure_schema()` is called at app startup. All DDL uses `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` — completely idempotent. Adding new columns to existing tables requires a manual `ALTER TABLE` or a new `ensure_schema()` pass.

---

## Key Design Decisions

### TW cost basis: raw transactions, not snapshots

`tw_transactions` stores the original 對帳單 rows (`share_delta`, `cost_flow` per trade). Net holdings are re-derived at read time by `_aggregate_tw_transactions()`:

```python
net_shares = Σ share_delta  (buys positive, sells negative)
net_cost   = Σ cost_flow    (buys positive, sells reduce basis)
cost_per_share = net_cost / net_shares
```

This approach preserves full transaction history and allows future per-period P&L analysis. Sells that bring `net_shares ≤ 0` drop the position automatically.

### US cost basis in TWD (`us_twd_cost`)

Stored as a single number in `user_config` under key `us_twd_cost`. This represents the total TWD actually wired to the overseas broker, and is used as a **fixed cost basis** that never changes with FX rates. Without it, the cost line on P&L charts drifts up/down as USD/TWD fluctuates even when no trades occur.

On every US CSV upload, `us_twd_cost` is auto-computed as:
```
Σ(shares × avg_cost_usd) × current_usd_twd_rate
```
Users can override this manually in the Holdings section.

### TW sell-cost factor

```python
TW_SELL_FACTOR = 1 - ((0.1425 * 0.28 + 0.1) / 100)  # ≈ 0.99860
```

Applied to TW market values in the Holdings table and KPI cards. Components:
- `0.1425%` brokerage commission × `28%` discounted rate → `0.03990%`
- `0.1%` ETF transaction tax (applies to ETFs; waived for individual stocks, but all tracked holdings are ETFs)

### Tab visibility

`db.has_user_data(username)` counts `tw_transactions + us_holdings > 0`. First-time users see only **Upload** and **Account** tabs; the **Dashboard** tab appears once data is uploaded.

### Username rename

`db.rename_user(old, new)` runs atomically in a single transaction:
1. Guard: check `new` doesn't already exist
2. `INSERT INTO users ... SELECT ... FROM users WHERE username = old`
3. `UPDATE` all 5 child tables (`tw_transactions`, `us_holdings`, `pledge_loans`, `user_config`, `portfolio_history`)
4. `DELETE FROM users WHERE username = old`

No `ON UPDATE CASCADE` needed because step 3 updates children before step 4 removes the parent.

### Price caching

```python
PRICE_CACHE_TTL   = 300   # 5 minutes (current prices + FX rate)
HISTORY_CACHE_TTL = 3600  # 1 hour (historical price series)
```

`fetch_usd_twd_rate()`: primary source is Cathay Bank's digital-channel billboard page (`pd.read_html` on the buying-rate row); yfinance `USDTWD=X` is the silent fallback.

`_cached_history()` in `app.py` takes JSON-serialised holdings as arguments (hashable for Streamlit's cache) and calls `compute_portfolio_history()`.

### CSV validation

`validate_csv_upload(buf, label)` checks in order:
1. File not empty
2. File size ≤ 10 MB
3. Parseable as CSV (UTF-8, UTF-8-sig, Big5, CP950 tried in order)
4. Row count ≤ 10,000
5. Column count ≤ 60

SQL injection is prevented by parameterised queries throughout `db.py` — no additional sanitisation needed beyond the structural checks above.

### Multi-file CSV merge

- **TW (對帳單)**: all rows from all files are concatenated, then deduped by `(symbol, trade_date, share_delta, cost_flow)`. The merged set replaces existing DB rows atomically.
- **US (複委託庫存)**: holdings are merged into a `dict` keyed by symbol; last file wins for duplicate symbols. Final dict values replace existing DB rows atomically.

### P&L history chart

`_chart_pnl_level()` uses `mark_line + mark_point` (not `mark_area`) with `scale=alt.Scale(zero=False)` so the Y-axis range tracks actual data — small fluctuations are visible even when P&L is large and positive.

### Pledge interest

`override_interest_twd` stored per loan. When set (not None), it's used directly as the accrued interest. When None, interest is computed as:
```
principal × rate% × days_elapsed / 365
```

---

## Adding / Changing Tickers

### Add a new TW ticker

Two files require changes:

1. **`utils/data_loader.py`**
   - `TW_NAME_TO_TICKER`: add `'中文名稱': 'SYMBOL'`
   - `TW_INCLUDE_FROM`: add `'SYMBOL': None` (or a date string `'YYYY-MM-DD'` to ignore transactions before that date — useful after fully selling and re-buying)

2. **`config/settings.py`**
   - `TW_TICKERS`: add `'SYMBOL': 'SYMBOL.TW'`

### Add a new US ticker

One file:

1. **`config/settings.py`**
   - `US_TICKERS`: add `'SYMBOL': 'SYMBOL'`

US tickers don't need name mapping because 複委託庫存 CSV already contains the symbol directly.

---

## CSV Format Reference

### 對帳單 (TW transaction history)

Detected by columns: `{'股名', '日期', '成交股數', '淨收付'}`

| Column | Type | Meaning |
|---|---|---|
| `股名` | str | Chinese name; mapped to ticker via `TW_NAME_TO_TICKER` |
| `日期` | date | Trade date |
| `成交股數` | int | Absolute shares traded |
| `淨收付` | float | Negative = buy (cash out), Positive = sell (cash in) |

`share_delta` = `+成交股數` for buys, `-成交股數` for sells.  
`cost_flow` = `|淨收付|` for buys, `-|淨收付|` for sells.

### 複委託庫存 (US holdings snapshot)

Detected by columns: `{'代號', '目前庫存', '均價'}`

| Column | Meaning |
|---|---|
| `代號` | Ticker symbol |
| `目前庫存` | Current shares held |
| `均價` | Average cost per share (USD) |

---

## Authentication

- **Algorithm**: PBKDF2-SHA256, 200,000 iterations, 32-byte random salt per user
- **No 2FA**: `totp_enabled` defaults to `False` for all new accounts; login requires only username + password
- **Session state**: `st.session_state.authenticated` and `st.session_state.username`; cleared on `logout()`
- **Username change**: `db.rename_user()` in a single DB transaction (see above)

`utils/auth.py` has no file I/O — all reads/writes go through `utils/db.py`.

---

## `app.py` Structure

```
show_auth()               Login + Create Account forms
render_dashboard()
  ├─ header + Refresh / Sign Out buttons
  ├─ tab_dash  (only if has_user_data)
  │    ├─ KPI metrics row (6 columns)
  │    ├─ _section_charts()       allocation pies
  │    ├─ _section_holdings()     holdings table + US cost editor
  │    ├─ _section_pnl_history()  P&L change charts (daily/monthly/annual)
  │    └─ _section_pledge()       pledge monitoring + loan editor
  ├─ tab_upload   _tab_upload()
  └─ tab_account  _tab_account()
```

Charts are rendered via `_render(chart, height)` which applies the global Altair dark theme (background transparent, axis/label colours, legend style).

---

## Deployment

Railway reads `railway.toml`. Required env vars in Railway dashboard:

| Variable | Description |
|---|---|
| `DATABASE_URL` | Neon PostgreSQL connection string (`postgresql://...?sslmode=require&channel_binding=require`) |
| `APP_SECRET_KEY` | Random hex string for session security |

The app calls `db.ensure_schema()` at startup — no manual schema creation or file seeding needed on first deploy.

---

## Dead Code (keep until confirmed working on Railway)

- `utils/gdrive.py` — Google Drive upload/download; replaced by Neon
- `utils/gsheets.py` — Google Sheets helpers; not used

Remove both files after confirming Railway deployment works end-to-end.

---

## Known Limitations

- `TW_INCLUDE_FROM['0050'] = '2025-06-18'` is hardcoded — update if new 0050 lots are purchased from an earlier date that should be included.
- Cathay Bank FX scraper silently falls back to yfinance if the page structure changes.
- P&L history chart uses `current holdings × historical price` (not transaction-based running position) — acceptable for long-term ETF holds where position rarely changes.
- Neon free tier auto-suspends after 5 min idle; first login after idle has ~0.5–2s cold-start delay. Handled by `_with_conn` reconnect logic.
- `ThreadedConnectionPool(maxconn=3)` is well below Neon's free-tier connection limit; raise if concurrent user count grows.
