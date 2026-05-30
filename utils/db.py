"""
utils/db.py — Neon PostgreSQL data layer.

Replaces utils/gdrive.py as the persistence backend.
All tables include a `username` column for per-user data isolation.

Connection strategy: ThreadedConnectionPool (Streamlit is multi-threaded)
with single-retry on Neon cold-start (idle timeout after 5 min on free tier).
"""
import os
import threading
from typing import Dict, List, Optional

import psycopg2
import psycopg2.pool
import psycopg2.extras
from psycopg2.extras import RealDictCursor

# ── Pool singleton ─────────────────────────────────────────────────────────────
_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()
_schema_initialized = False


def _build_pool() -> psycopg2.pool.ThreadedConnectionPool:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Add it to your .env file or Railway settings."
        )
    return psycopg2.pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=3,
        dsn=dsn,
        connect_timeout=10,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )


def _ensure_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    with _pool_lock:
        if _pool is None or _pool.closed:
            _pool = _build_pool()
    return _pool


def _with_conn(func):
    """
    Run func(conn) with a pooled connection.
    Automatically reconnects once on Neon cold-start (OperationalError after 5-min idle).
    func receives a live psycopg2 connection; it must NOT call commit/rollback.
    """
    global _pool

    def _attempt(pool):
        conn = pool.getconn()
        try:
            result = func(conn)
            conn.commit()
            pool.putconn(conn)
            return result
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                pool.putconn(conn)
            except Exception:
                pass
            raise

    pool = _ensure_pool()
    try:
        return _attempt(pool)
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        # Connection is dead (Neon auto-suspend). Rebuild pool and retry once.
        with _pool_lock:
            try:
                _pool.closeall()
            except Exception:
                pass
            _pool = _build_pool()
            pool = _pool
        return _attempt(pool)


# ── Schema bootstrap ───────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS users (
    username      TEXT             PRIMARY KEY,
    password_hash TEXT             NOT NULL,
    salt          TEXT             NOT NULL,
    totp_secret   TEXT,
    totp_enabled  BOOLEAN          NOT NULL DEFAULT FALSE,
    email         TEXT             NOT NULL DEFAULT '',
    created_at    DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tw_transactions (
    id           BIGSERIAL      PRIMARY KEY,
    username     TEXT           NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    symbol       TEXT           NOT NULL,
    name         TEXT           NOT NULL DEFAULT '',
    trade_date   DATE           NOT NULL,
    share_delta  NUMERIC(14,4)  NOT NULL,
    cost_flow    NUMERIC(14,4)  NOT NULL,
    uploaded_at  TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tw_txn_user_sym
    ON tw_transactions (username, symbol, trade_date);

CREATE TABLE IF NOT EXISTS us_holdings (
    id             BIGSERIAL      PRIMARY KEY,
    username       TEXT           NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    symbol         TEXT           NOT NULL,
    name           TEXT           NOT NULL DEFAULT '',
    shares         NUMERIC(14,4)  NOT NULL,
    cost_per_share NUMERIC(14,6)  NOT NULL,
    currency       TEXT           NOT NULL DEFAULT 'USD',
    uploaded_at    TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    UNIQUE (username, symbol)
);
CREATE INDEX IF NOT EXISTS idx_us_holdings_user ON us_holdings (username);

CREATE TABLE IF NOT EXISTS pledge_loans (
    id                    BIGSERIAL      PRIMARY KEY,
    username              TEXT           NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    loan_seq              INTEGER        NOT NULL,
    description           TEXT           NOT NULL DEFAULT '',
    loan_amount_twd       NUMERIC(16,2)  NOT NULL DEFAULT 0,
    interest_rate         NUMERIC(8,4)   NOT NULL DEFAULT 0,
    start_date            DATE,
    expiry_date           DATE,
    override_interest_twd NUMERIC(16,2),
    UNIQUE (username, loan_seq)
);
CREATE INDEX IF NOT EXISTS idx_pledge_loans_user ON pledge_loans (username);

CREATE TABLE IF NOT EXISTS pledge_stocks (
    id       BIGSERIAL PRIMARY KEY,
    loan_id  BIGINT    NOT NULL REFERENCES pledge_loans(id) ON DELETE CASCADE,
    symbol   TEXT      NOT NULL,
    shares   INTEGER   NOT NULL,
    currency TEXT      NOT NULL DEFAULT 'TWD'
);
CREATE INDEX IF NOT EXISTS idx_pledge_stocks_loan ON pledge_stocks (loan_id);

CREATE TABLE IF NOT EXISTS user_config (
    username   TEXT           NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    key        TEXT           NOT NULL,
    value_num  NUMERIC(20,4),
    updated_at TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    PRIMARY KEY (username, key)
);

CREATE TABLE IF NOT EXISTS portfolio_history (
    username        TEXT          NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    date            DATE          NOT NULL,
    total_value_twd NUMERIC(20,4) NOT NULL,
    total_pnl_twd   NUMERIC(20,4) NOT NULL,
    pnl_pct         NUMERIC(10,6) NOT NULL,
    PRIMARY KEY (username, date)
);
CREATE INDEX IF NOT EXISTS idx_ph_user_date
    ON portfolio_history (username, date DESC);
"""


def ensure_schema() -> None:
    """Create all tables if they don't exist. Idempotent; fast after first call."""
    global _schema_initialized
    if _schema_initialized:
        return

    def _run(conn):
        with conn.cursor() as cur:
            cur.execute(_DDL)

    _with_conn(_run)
    _schema_initialized = True


# ── Users ──────────────────────────────────────────────────────────────────────

def get_user(username: str) -> Optional[Dict]:
    """Return full user row as dict, or None if not found."""
    def _run(conn):
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE username = %s", (username,))
            row = cur.fetchone()
            return dict(row) if row else None
    return _with_conn(_run)


def list_usernames() -> List[str]:
    """Return list of all usernames."""
    def _run(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT username FROM users ORDER BY username")
            return [r[0] for r in cur.fetchall()]
    return _with_conn(_run)


def upsert_user(username: str, password_hash: str, salt: str,
                totp_secret: Optional[str], totp_enabled: bool,
                email: str, created_at: float) -> None:
    def _run(conn):
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users
                    (username, password_hash, salt, totp_secret, totp_enabled, email, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (username) DO UPDATE
                SET password_hash = EXCLUDED.password_hash,
                    salt          = EXCLUDED.salt,
                    totp_secret   = EXCLUDED.totp_secret,
                    totp_enabled  = EXCLUDED.totp_enabled,
                    email         = EXCLUDED.email
            """, (username, password_hash, salt, totp_secret, totp_enabled, email, created_at))
    _with_conn(_run)


def update_user_password(username: str, password_hash: str, salt: str) -> None:
    def _run(conn):
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET password_hash = %s, salt = %s WHERE username = %s",
                (password_hash, salt, username),
            )
    _with_conn(_run)


def update_user_email(username: str, email: str) -> None:
    def _run(conn):
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET email = %s WHERE username = %s",
                (email, username),
            )
    _with_conn(_run)


# ── TW Transactions ────────────────────────────────────────────────────────────

def replace_tw_transactions(username: str, rows: List[Dict]) -> None:
    """
    Atomically replace all TW transactions for this user.
    rows: [{symbol, name, trade_date (str YYYY-MM-DD), share_delta, cost_flow}]
    """
    def _run(conn):
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM tw_transactions WHERE username = %s", (username,)
            )
            if rows:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO tw_transactions
                        (username, symbol, name, trade_date, share_delta, cost_flow)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, [
                    (username, r["symbol"], r["name"],
                     r["trade_date"], r["share_delta"], r["cost_flow"])
                    for r in rows
                ])
    _with_conn(_run)


def get_tw_transactions(username: str) -> List[Dict]:
    """Return all TW transaction rows for this user, ordered by trade_date ASC."""
    def _run(conn):
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT symbol, name, trade_date::text AS trade_date,
                       share_delta::float, cost_flow::float
                FROM tw_transactions
                WHERE username = %s
                ORDER BY trade_date ASC, id ASC
            """, (username,))
            return [dict(r) for r in cur.fetchall()]
    return _with_conn(_run)


# ── US Holdings ────────────────────────────────────────────────────────────────

def replace_us_holdings(username: str, rows: List[Dict]) -> None:
    """
    Atomically replace all US holdings for this user.
    rows: [{symbol, name, shares, cost_per_share, currency}]
    """
    def _run(conn):
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM us_holdings WHERE username = %s", (username,)
            )
            if rows:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO us_holdings
                        (username, symbol, name, shares, cost_per_share, currency)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, [
                    (username, r["symbol"], r.get("name", r["symbol"]),
                     r["shares"], r["cost_per_share"], r.get("currency", "USD"))
                    for r in rows
                ])
    _with_conn(_run)


def get_us_holdings(username: str) -> List[Dict]:
    """Return current US holdings for this user."""
    def _run(conn):
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT symbol, name, shares::float, cost_per_share::float, currency
                FROM us_holdings
                WHERE username = %s
                ORDER BY symbol
            """, (username,))
            return [dict(r) for r in cur.fetchall()]
    return _with_conn(_run)


# ── Pledge Config ──────────────────────────────────────────────────────────────

def get_pledge_config(username: str) -> Dict:
    """Reconstruct {'loans': [...]} from pledge_loans + pledge_stocks tables."""
    def _run(conn):
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    pl.id          AS loan_db_id,
                    pl.loan_seq,
                    pl.description,
                    pl.loan_amount_twd::float,
                    pl.interest_rate::float,
                    pl.start_date::text  AS start_date,
                    pl.expiry_date::text AS expiry_date,
                    pl.override_interest_twd,
                    ps.symbol,
                    ps.shares,
                    ps.currency
                FROM pledge_loans pl
                LEFT JOIN pledge_stocks ps ON ps.loan_id = pl.id
                WHERE pl.username = %s
                ORDER BY pl.loan_seq, ps.id
            """, (username,))
            rows = cur.fetchall()

        if not rows:
            return {"loans": []}

        loans_dict: Dict[int, Dict] = {}
        for row in rows:
            seq = row["loan_seq"]
            if seq not in loans_dict:
                oi = row["override_interest_twd"]
                loans_dict[seq] = {
                    "id":                    seq,
                    "description":           row["description"] or "",
                    "loan_amount_twd":       float(row["loan_amount_twd"] or 0),
                    "interest_rate":         float(row["interest_rate"] or 0),
                    "date":                  row["start_date"] or "",
                    "expiry_date":           row["expiry_date"] or "",
                    "override_interest_twd": float(oi) if oi is not None else None,
                    "pledged_stocks":        [],
                }
            if row["symbol"]:
                loans_dict[seq]["pledged_stocks"].append({
                    "symbol":   row["symbol"],
                    "shares":   int(row["shares"] or 0),
                    "currency": row["currency"] or "TWD",
                })

        return {"loans": list(loans_dict.values())}

    return _with_conn(_run)


def save_pledge_config(username: str, config: Dict) -> None:
    """Atomically replace all pledge data for this user."""
    loans = config.get("loans", [])

    def _run(conn):
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM pledge_loans WHERE username = %s", (username,)
            )
            for seq, loan in enumerate(loans, start=1):
                start_date  = loan.get("date") or None
                expiry_date = loan.get("expiry_date") or None
                if start_date  == "": start_date  = None
                if expiry_date == "": expiry_date = None
                oi = loan.get("override_interest_twd")

                cur.execute("""
                    INSERT INTO pledge_loans
                        (username, loan_seq, description, loan_amount_twd,
                         interest_rate, start_date, expiry_date, override_interest_twd)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    username, seq,
                    loan.get("description", ""),
                    float(loan.get("loan_amount_twd", 0) or 0),
                    float(loan.get("interest_rate", 0) or 0),
                    start_date, expiry_date,
                    float(oi) if oi is not None else None,
                ))
                loan_db_id = cur.fetchone()[0]

                for stock in loan.get("pledged_stocks", []):
                    cur.execute("""
                        INSERT INTO pledge_stocks (loan_id, symbol, shares, currency)
                        VALUES (%s, %s, %s, %s)
                    """, (
                        loan_db_id,
                        stock["symbol"],
                        int(stock["shares"]),
                        stock.get("currency", "TWD"),
                    ))

    _with_conn(_run)


# ── User Config (key-value) ────────────────────────────────────────────────────

def get_user_config_num(username: str, key: str, default: float = 0.0) -> float:
    """Fetch a numeric config value; return default if not set."""
    def _run(conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT value_num FROM user_config WHERE username = %s AND key = %s",
                (username, key),
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                return float(row[0])
            return default
    return _with_conn(_run)


def set_user_config_num(username: str, key: str, value: float) -> None:
    def _run(conn):
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_config (username, key, value_num, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (username, key) DO UPDATE
                SET value_num  = EXCLUDED.value_num,
                    updated_at = NOW()
            """, (username, key, value))
    _with_conn(_run)


# ── Portfolio History ──────────────────────────────────────────────────────────

def upsert_history_snapshot(username: str, date_str: str,
                             total_value_twd: float,
                             total_pnl_twd: float,
                             pnl_pct: float) -> None:
    """Upsert today's snapshot and trim to a rolling 730-day window."""
    def _run(conn):
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO portfolio_history
                    (username, date, total_value_twd, total_pnl_twd, pnl_pct)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (username, date) DO UPDATE
                SET total_value_twd = EXCLUDED.total_value_twd,
                    total_pnl_twd   = EXCLUDED.total_pnl_twd,
                    pnl_pct         = EXCLUDED.pnl_pct
            """, (username, date_str, total_value_twd, total_pnl_twd, pnl_pct))
            # Rolling 730-day window
            cur.execute("""
                DELETE FROM portfolio_history
                WHERE username = %s
                  AND date < CURRENT_DATE - INTERVAL '730 days'
            """, (username,))
    _with_conn(_run)


def get_history(username: str) -> List[Dict]:
    """Return all portfolio history rows for this user, ordered by date ASC."""
    def _run(conn):
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT date::text AS date,
                       total_value_twd::float,
                       total_pnl_twd::float,
                       pnl_pct::float
                FROM portfolio_history
                WHERE username = %s
                ORDER BY date ASC
            """, (username,))
            return [dict(r) for r in cur.fetchall()]
    return _with_conn(_run)


# ── Utility ────────────────────────────────────────────────────────────────────

def has_user_data(username: str) -> bool:
    """Return True if the user has uploaded any TW transactions or US holdings."""
    def _run(conn):
        with conn.cursor() as cur:
            cur.execute("""
                SELECT (
                    (SELECT COUNT(*) FROM tw_transactions WHERE username = %s) +
                    (SELECT COUNT(*) FROM us_holdings     WHERE username = %s)
                ) > 0
            """, (username, username))
            return bool(cur.fetchone()[0])
    return _with_conn(_run)


def rename_user(old_username: str, new_username: str) -> None:
    """
    Rename a user atomically: insert new record, migrate all child rows, delete old.
    Raises ValueError if new_username already exists.
    """
    def _run(conn):
        with conn.cursor() as cur:
            # Guard: ensure new username doesn't already exist
            cur.execute("SELECT 1 FROM users WHERE username = %s", (new_username,))
            if cur.fetchone():
                raise ValueError(f"Username '{new_username}' already exists")

            # Insert new user row (copy from old)
            cur.execute("""
                INSERT INTO users
                    (username, password_hash, salt, totp_secret, totp_enabled, email, created_at)
                SELECT %s, password_hash, salt, totp_secret, totp_enabled, email, created_at
                FROM users WHERE username = %s
            """, (new_username, old_username))

            # Update all child tables
            for table in ("tw_transactions", "us_holdings", "pledge_loans",
                          "user_config", "portfolio_history"):
                cur.execute(
                    f"UPDATE {table} SET username = %s WHERE username = %s",
                    (new_username, old_username),
                )

            # Delete old user (no child rows remain, no cascade needed)
            cur.execute("DELETE FROM users WHERE username = %s", (old_username,))

    _with_conn(_run)
