"""
migrate_to_neon.py — One-time migration from flat files to Neon PostgreSQL.

Reads existing data/users.json, data/tw_stocks.csv, data/us_stocks.csv,
data/pledge_config.json, data/us_cost_config.json, data/portfolio_history.json
and imports everything into the DB.

Safe to run multiple times (all operations are upserts).

Usage:
    python migrate_to_neon.py
"""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# Ensure schema exists first
from utils.db import (
    ensure_schema,
    upsert_user,
    list_usernames,
    replace_tw_transactions,
    replace_us_holdings,
    save_pledge_config,
    set_user_config_num,
    upsert_history_snapshot,
)
from utils.data_loader import (
    _read_csv,
    _is_dazhangdan,
    _is_fuzhuotuo,
    _parse_dazhangdan_rows,
    _parse_fuzhuotuo,
)

DATA_DIR = Path("data")


def migrate():
    print("─" * 50)
    print("Neon PostgreSQL Migration")
    print("─" * 50)

    print("\n[1/6] Creating schema...")
    ensure_schema()
    print("     Schema OK.")

    # ── 1. Users ──────────────────────────────────────────────────────────────
    print("\n[2/6] Migrating users...")
    users_file = DATA_DIR / "users.json"
    if not users_file.exists():
        print("     SKIP: data/users.json not found.")
        users = {}
    else:
        users = json.loads(users_file.read_text(encoding="utf-8"))
        for uname, u in users.items():
            upsert_user(
                username=uname,
                password_hash=u["password_hash"],
                salt=u["salt"],
                totp_secret=u.get("totp_secret"),
                totp_enabled=u.get("totp_enabled", False),
                email=u.get("email", ""),
                created_at=u.get("created_at", 0.0),
            )
        print(f"     ✅ {len(users)} user(s) migrated.")

    # Pick the primary user (first in the file; usually the only one)
    all_db_users = list_usernames()
    if not all_db_users:
        print("\nERROR: No users in DB after migration. Nothing to assign data to.")
        sys.exit(1)
    primary = list(users.keys())[0] if users else all_db_users[0]
    print(f"\n     Primary user for data assignment: '{primary}'")

    # ── 2. TW Transactions ────────────────────────────────────────────────────
    print("\n[3/6] Migrating TW transactions (對帳單)...")
    tw_path = DATA_DIR / "tw_stocks.csv"
    if not tw_path.exists():
        print("     SKIP: data/tw_stocks.csv not found.")
    else:
        df = _read_csv(tw_path)
        if df is None:
            print("     SKIP: Could not read tw_stocks.csv (encoding issue?).")
        elif not _is_dazhangdan(df):
            print("     SKIP: tw_stocks.csv is not in 對帳單 format.")
        else:
            rows = _parse_dazhangdan_rows(df)
            if rows:
                replace_tw_transactions(primary, rows)
                print(f"     ✅ {len(rows)} transaction rows migrated for '{primary}'.")
            else:
                print("     WARN: No valid transactions parsed from tw_stocks.csv.")

    # ── 3. US Holdings ────────────────────────────────────────────────────────
    print("\n[4/6] Migrating US holdings (複委託庫存)...")
    us_path = DATA_DIR / "us_stocks.csv"
    if not us_path.exists():
        print("     SKIP: data/us_stocks.csv not found.")
    else:
        df = _read_csv(us_path)
        if df is None:
            print("     SKIP: Could not read us_stocks.csv.")
        elif not _is_fuzhuotuo(df):
            print("     SKIP: us_stocks.csv is not in 複委託庫存 format.")
        else:
            holdings = _parse_fuzhuotuo(df) or []
            if holdings:
                replace_us_holdings(primary, holdings)
                print(f"     ✅ {len(holdings)} US holdings migrated for '{primary}'.")
            else:
                print("     WARN: No valid holdings parsed from us_stocks.csv.")

    # ── 4. Pledge Config ──────────────────────────────────────────────────────
    print("\n[5/6] Migrating pledge config...")
    pledge_path = DATA_DIR / "pledge_config.json"
    cost_path   = DATA_DIR / "us_cost_config.json"

    if not pledge_path.exists():
        print("     SKIP: data/pledge_config.json not found.")
    else:
        config = json.loads(pledge_path.read_text(encoding="utf-8"))
        loans = config.get("loans", [])
        save_pledge_config(primary, config)
        print(f"     ✅ {len(loans)} pledge loan(s) migrated for '{primary}'.")

    if not cost_path.exists():
        print("     SKIP: data/us_cost_config.json not found.")
    else:
        data = json.loads(cost_path.read_text(encoding="utf-8"))
        val  = float(data.get("us_twd_cost", 0))
        if val > 0:
            set_user_config_num(primary, "us_twd_cost", val)
            print(f"     ✅ US TWD cost = {val:,.0f} migrated for '{primary}'.")
        else:
            print("     SKIP: us_twd_cost is 0.")

    # ── 5. Portfolio History ──────────────────────────────────────────────────
    print("\n[6/6] Migrating portfolio history...")
    hist_path = DATA_DIR / "portfolio_history.json"
    if not hist_path.exists():
        print("     SKIP: data/portfolio_history.json not found.")
    else:
        history = json.loads(hist_path.read_text(encoding="utf-8"))
        for entry in history:
            upsert_history_snapshot(
                username=primary,
                date_str=entry["date"],
                total_value_twd=entry["total_value_twd"],
                total_pnl_twd=entry["total_pnl_twd"],
                pnl_pct=entry["pnl_pct"],
            )
        print(f"     ✅ {len(history)} history entries migrated for '{primary}'.")

    print("\n" + "─" * 50)
    print("✅ Migration complete.")
    print("─" * 50)


if __name__ == "__main__":
    migrate()
