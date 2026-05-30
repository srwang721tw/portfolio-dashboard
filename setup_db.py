"""
setup_db.py — Create Neon PostgreSQL schema (idempotent).

Run once before first deploy, or any time to verify schema is up to date:
    python setup_db.py
"""
from dotenv import load_dotenv
load_dotenv()

from utils.db import ensure_schema, _build_pool

if __name__ == "__main__":
    print("Connecting to Neon PostgreSQL...")
    try:
        # Force a real connection attempt so we get a clear error if DATABASE_URL is wrong
        pool = _build_pool()
        pool.closeall()
        print("Connection OK.")
    except Exception as e:
        print(f"ERROR: Could not connect — {e}")
        raise SystemExit(1)

    print("Creating schema...")
    ensure_schema()
    print("✅ Schema ready.")
