from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import pytz

import utils.db as db

TZ = pytz.timezone("Asia/Taipei")


def _today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def load_history(username: str) -> List[Dict]:
    return db.get_history(username)


def save_snapshot(username: str, total_value_twd: float,
                  total_pnl_twd: float, pnl_pct: float):
    """Save or update today's portfolio snapshot for this user."""
    db.upsert_history_snapshot(username, _today_str(),
                               total_value_twd, total_pnl_twd, pnl_pct)


def history_to_dataframe(username: str) -> pd.DataFrame:
    history = load_history(username)
    if not history:
        return pd.DataFrame()
    df = pd.DataFrame(history)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df["daily_pnl_change"] = df["total_pnl_twd"].diff()
    return df


def get_pnl_change(username: str, days: int = 1) -> Optional[float]:
    """Return P&L change over last N days from stored history."""
    df = history_to_dataframe(username)
    if df.empty or len(df) < 2:
        return None
    recent = df["total_pnl_twd"].dropna()
    if len(recent) < days + 1:
        start_val = recent.iloc[0]
    else:
        start_val = recent.iloc[-(days + 1)]
    end_val = recent.iloc[-1]
    return end_val - start_val
