"""
CSV loader for 國泰證券 format.

Taiwan CSV expected columns (flexible matching):
  股票代號, 股票名稱, 庫存股數, 平均成本, 成本金額

US CSV expected columns (flexible matching):
  代號/Symbol, 名稱/Name, 股數/Shares, 成本均價/Avg Cost, 投資成本/Total Cost
"""

import json
from pathlib import Path
from typing import List, Dict, Optional

import pandas as pd
import streamlit as st

from config.settings import (
    TW_CSV_FILE, US_CSV_FILE, SAMPLE_TW_CSV, SAMPLE_US_CSV,
    PLEDGE_FILE, TW_TICKERS, US_TICKERS,
)

# Column name aliases for flexible parsing
TW_COL_ALIASES = {
    "symbol":     ["股票代號", "代號", "symbol", "ticker", "code"],
    "name":       ["股票名稱", "名稱", "name", "stock_name"],
    "shares":     ["庫存股數", "股數", "shares", "quantity", "持股"],
    "cost":       ["平均成本", "成本均價", "avg_cost", "average_cost", "均價"],
    "total_cost": ["成本金額", "投資成本", "total_cost", "cost_amount"],
}

US_COL_ALIASES = {
    "symbol":     ["symbol", "代號", "ticker", "股票代號"],
    "name":       ["name", "名稱", "stock_name", "股票名稱"],
    "shares":     ["shares", "股數", "quantity", "庫存股數"],
    "cost":       ["avg cost", "avg cost (usd)", "average cost", "成本均價", "avg_cost"],
    "total_cost": ["total cost", "total cost (usd)", "投資成本", "cost_amount"],
}


def _match_col(df_cols: List[str], aliases: List[str]) -> Optional[str]:
    lower_cols = {c.lower().strip(): c for c in df_cols}
    for alias in aliases:
        if alias.lower() in lower_cols:
            return lower_cols[alias.lower()]
    return None


def _parse_csv(path: Path, col_aliases: Dict) -> Optional[pd.DataFrame]:
    try:
        # Try UTF-8 first, then BIG5 for traditional Chinese
        for enc in ["utf-8-sig", "big5", "cp950", "utf-8"]:
            try:
                # Skip header rows that don't look like column names
                df_raw = pd.read_csv(path, encoding=enc, header=None)
                # Find the header row (row with most matching aliases)
                header_row = 0
                for i, row in df_raw.iterrows():
                    row_vals = [str(v).lower().strip() for v in row.values]
                    matches = sum(
                        1 for aliases in col_aliases.values()
                        for alias in aliases
                        if alias.lower() in row_vals
                    )
                    if matches >= 2:
                        header_row = i
                        break
                df = pd.read_csv(path, encoding=enc, skiprows=header_row)
                break
            except UnicodeDecodeError:
                continue
        else:
            return None

        cols = list(df.columns)
        result = pd.DataFrame()
        for field, aliases in col_aliases.items():
            matched = _match_col(cols, aliases)
            if matched:
                result[field] = df[matched]

        if "symbol" not in result.columns or "shares" not in result.columns:
            return None

        result = result.dropna(subset=["symbol", "shares"])
        result["symbol"] = result["symbol"].astype(str).str.strip()
        result["shares"] = pd.to_numeric(result["shares"].astype(str).str.replace(",", ""), errors="coerce")
        if "cost" in result.columns:
            result["cost"] = pd.to_numeric(result["cost"].astype(str).str.replace(",", ""), errors="coerce")
        if "total_cost" in result.columns:
            result["total_cost"] = pd.to_numeric(result["total_cost"].astype(str).str.replace(",", ""), errors="coerce")

        result = result[result["shares"] > 0].reset_index(drop=True)
        return result

    except Exception:
        return None


def load_tw_holdings() -> List[Dict]:
    path = TW_CSV_FILE if TW_CSV_FILE.exists() else SAMPLE_TW_CSV
    df = _parse_csv(path, TW_COL_ALIASES)
    if df is None:
        return _sample_tw_holdings()

    holdings = []
    for _, row in df.iterrows():
        symbol = str(row["symbol"]).zfill(4) if str(row["symbol"]).isdigit() else str(row["symbol"])
        if symbol not in TW_TICKERS and f"0{symbol}" not in TW_TICKERS:
            pass  # Allow any symbol, not just known ones
        shares = int(row.get("shares", 0))
        cost = float(row.get("cost", 0) or 0)
        total = float(row.get("total_cost", 0) or 0)
        if cost == 0 and total > 0 and shares > 0:
            cost = total / shares
        holdings.append({
            "symbol": symbol,
            "name": str(row.get("name", symbol)),
            "shares": shares,
            "cost_per_share": cost,
            "currency": "TWD",
        })
    return holdings or _sample_tw_holdings()


def load_us_holdings() -> List[Dict]:
    path = US_CSV_FILE if US_CSV_FILE.exists() else SAMPLE_US_CSV
    df = _parse_csv(path, US_COL_ALIASES)
    if df is None:
        return _sample_us_holdings()

    holdings = []
    for _, row in df.iterrows():
        symbol = str(row["symbol"]).upper().strip()
        shares = float(row.get("shares", 0))
        cost = float(row.get("cost", 0) or 0)
        total = float(row.get("total_cost", 0) or 0)
        if cost == 0 and total > 0 and shares > 0:
            cost = total / shares
        holdings.append({
            "symbol": symbol,
            "name": str(row.get("name", symbol)),
            "shares": shares,
            "cost_per_share": cost,
            "currency": "USD",
        })
    return holdings or _sample_us_holdings()


def load_pledge_config() -> Dict:
    if PLEDGE_FILE.exists():
        with open(PLEDGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"loans": []}


def save_pledge_config(config: Dict):
    PLEDGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PLEDGE_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    try:
        from utils.gdrive import upload
        upload(PLEDGE_FILE)
    except Exception:
        pass


def _sample_tw_holdings() -> List[Dict]:
    return [
        {"symbol": "0050",   "name": "元大台灣50",       "shares": 1000, "cost_per_share": 155.0,  "currency": "TWD"},
        {"symbol": "006208", "name": "富邦台50",          "shares": 2000, "cost_per_share": 88.5,   "currency": "TWD"},
        {"symbol": "00713",  "name": "元大台灣高息低波",  "shares": 3000, "cost_per_share": 52.3,   "currency": "TWD"},
    ]


def _sample_us_holdings() -> List[Dict]:
    return [
        {"symbol": "QQQM", "name": "Invesco Nasdaq 100 ETF",         "shares": 50,  "cost_per_share": 175.0, "currency": "USD"},
        {"symbol": "VT",   "name": "Vanguard Total World Stock ETF", "shares": 100, "cost_per_share": 98.5,  "currency": "USD"},
    ]
