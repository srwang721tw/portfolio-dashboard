import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import pytz

from config.settings import HISTORY_FILE

TZ = pytz.timezone("Asia/Taipei")


def _today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def load_history() -> List[Dict]:
    if not HISTORY_FILE.exists():
        return []
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_snapshot(total_value_twd: float, total_pnl_twd: float, pnl_pct: float):
    """Save or update today's portfolio snapshot."""
    history = load_history()
    today = _today_str()

    # Update existing entry for today or append new one
    existing = next((h for h in history if h["date"] == today), None)
    if existing:
        existing.update({
            "total_value_twd": total_value_twd,
            "total_pnl_twd": total_pnl_twd,
            "pnl_pct": pnl_pct,
        })
    else:
        history.append({
            "date": today,
            "total_value_twd": total_value_twd,
            "total_pnl_twd": total_pnl_twd,
            "pnl_pct": pnl_pct,
        })

    # Keep last 730 days
    history = sorted(history, key=lambda x: x["date"])[-730:]
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


def history_to_dataframe() -> pd.DataFrame:
    history = load_history()
    if not history:
        return pd.DataFrame()
    df = pd.DataFrame(history)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df["daily_pnl_change"] = df["total_pnl_twd"].diff()
    return df


def get_pnl_change(days: int = 1) -> Optional[float]:
    """Return P&L change over last N days from stored history."""
    df = history_to_dataframe()
    if df.empty or len(df) < 2:
        return None
    recent = df["total_pnl_twd"].dropna()
    if len(recent) < days + 1:
        start_val = recent.iloc[0]
    else:
        start_val = recent.iloc[-(days + 1)]
    end_val = recent.iloc[-1]
    return end_val - start_val
