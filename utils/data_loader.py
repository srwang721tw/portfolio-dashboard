"""
CSV loader for 國泰證券 format.

Handles two input formats automatically:
  1. 對帳單 (transaction history): 股名, 日期, 成交股數, 淨收付, ...
  2. 複委託庫存 (US holdings snapshot): 代號, 目前庫存, 均價, 庫存成本, ...
  3. Simple summary: symbol/代號, shares, cost/均價, ...  (fallback)
"""

import json
from pathlib import Path
from typing import List, Dict, Optional

import pandas as pd

from config.settings import (
    TW_CSV_FILE, US_CSV_FILE, SAMPLE_TW_CSV, SAMPLE_US_CSV,
    PLEDGE_FILE,
)

# ── Taiwan stock name → ticker code ──────────────────────────────────────────
TW_NAME_TO_TICKER: Dict[str, str] = {
    '元大台灣50':      '0050',
    '富邦台50':        '006208',
    '元大台灣高息低波': '00713',
    '國泰永續高股息':   '00878',
    '元大高股息':      '0056',
    '中信中國50':      '00752',
    '中信中國高股息':   '00882',
    '永豐優息存股':     '00907',
}

# Stocks to include and optional cutoff date (None = all history)
# '0050' starts from 2025-06-18 because earlier lots were fully sold.
TW_INCLUDE_FROM: Dict[str, Optional[str]] = {
    '0050':   '2025-06-18',
    '006208': None,
    '00713':  None,
}

# ── Column aliases for generic summary CSVs ───────────────────────────────────
_TW_COL_ALIASES = {
    "symbol":     ["股票代號", "代號", "symbol", "ticker", "code"],
    "name":       ["股票名稱", "名稱", "name", "stock_name"],
    "shares":     ["庫存股數", "股數", "shares", "quantity", "持股"],
    "cost":       ["平均成本", "成本均價", "avg_cost", "average_cost", "均價"],
    "total_cost": ["成本金額", "投資成本", "total_cost", "cost_amount"],
}

_US_COL_ALIASES = {
    "symbol":     ["symbol", "代號", "ticker", "股票代號"],
    "name":       ["name", "名稱", "stock_name", "股票名稱"],
    "shares":     ["shares", "股數", "quantity", "庫存股數", "目前庫存", "可用庫存"],
    "cost":       ["avg cost", "avg cost (usd)", "average cost", "成本均價",
                   "avg_cost", "均價"],
    "total_cost": ["total cost", "total cost (usd)", "投資成本", "cost_amount",
                   "庫存成本"],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_csv(path: Path) -> Optional[pd.DataFrame]:
    for enc in ["utf-8", "utf-8-sig", "big5", "cp950"]:
        try:
            return pd.read_csv(path, encoding=enc)
        except (UnicodeDecodeError, Exception):
            continue
    return None


def _clean_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(",", ""), errors="coerce")


def _match_col(df_cols: List[str], aliases: List[str]) -> Optional[str]:
    lower = {c.lower().strip(): c for c in df_cols}
    for alias in aliases:
        if alias.lower() in lower:
            return lower[alias.lower()]
    return None


# ── Format detectors ──────────────────────────────────────────────────────────

def _is_dazhangdan(df: pd.DataFrame) -> bool:
    """對帳單: has 股名 + 日期 + 成交股數 + 淨收付."""
    cols = set(df.columns.str.strip())
    return {'股名', '日期', '成交股數', '淨收付'}.issubset(cols)


def _is_fuzhuotuo(df: pd.DataFrame) -> bool:
    """複委託庫存: has 代號 + 目前庫存 + 均價."""
    cols = set(df.columns.str.strip())
    return {'代號', '目前庫存', '均價'}.issubset(cols)


# ── Format-specific parsers ───────────────────────────────────────────────────

def _parse_dazhangdan(df: pd.DataFrame) -> Optional[List[Dict]]:
    """
    Process 對帳單 transaction history into net current holdings.

    Logic mirrors TWD.ipynb:
      - Map stock names to ticker codes via TW_NAME_TO_TICKER
      - Apply per-ticker cutoff dates from TW_INCLUDE_FROM
      - Net shares: buys (淨收付 < 0) add shares; sells subtract shares
      - Cost basis: sum of abs(淨收付) for buys minus proceeds from sells
    """
    try:
        df = df.copy()
        df.columns = df.columns.str.strip()
        df['股名'] = df['股名'].str.strip()
        df['代號'] = df['股名'].map(TW_NAME_TO_TICKER)
        df = df.dropna(subset=['代號'])

        df['日期'] = pd.to_datetime(df['日期'], errors='coerce')
        df['成交股數'] = _clean_num(df['成交股數'])
        df['淨收付']   = _clean_num(df['淨收付'])
        df = df.dropna(subset=['日期', '成交股數', '淨收付'])

        # Apply per-ticker date filters
        frames = []
        for ticker, since in TW_INCLUDE_FROM.items():
            mask = df['代號'] == ticker
            if since:
                mask &= df['日期'] >= pd.Timestamp(since)
            chunk = df[mask]
            if not chunk.empty:
                frames.append(chunk)

        if not frames:
            return None

        filtered = pd.concat(frames, ignore_index=True)

        # Buy = 淨收付 < 0 (cash outflow); Sell = 淨收付 > 0
        filtered['is_buy']      = filtered['淨收付'] < 0
        filtered['share_delta'] = filtered.apply(
            lambda r: r['成交股數'] if r['is_buy'] else -r['成交股數'], axis=1
        )
        filtered['cost_flow'] = filtered['淨收付'].abs()
        filtered.loc[~filtered['is_buy'], 'cost_flow'] *= -1  # sells reduce cost basis

        name_rev = {v: k for k, v in TW_NAME_TO_TICKER.items()}
        holdings = []
        for ticker, grp in filtered.groupby('代號'):
            net_shares = grp['share_delta'].sum()
            net_cost   = grp['cost_flow'].sum()
            if net_shares <= 0 or net_cost <= 0:
                continue
            holdings.append({
                'symbol':         ticker,
                'name':           name_rev.get(ticker, ticker),
                'shares':         int(net_shares),
                'cost_per_share': round(net_cost / net_shares, 4),
                'currency':       'TWD',
            })

        return holdings or None

    except Exception:
        return None


def _parse_fuzhuotuo(df: pd.DataFrame) -> Optional[List[Dict]]:
    """Parse 複委託庫存 snapshot into holdings."""
    try:
        df = df.copy()
        df.columns = df.columns.str.strip()
        df['代號']    = df['代號'].astype(str).str.strip().str.upper()
        df['目前庫存'] = _clean_num(df['目前庫存'])
        df['均價']     = _clean_num(df['均價'])
        df = df.dropna(subset=['代號', '目前庫存', '均價'])
        df = df[df['目前庫存'] > 0]

        holdings = []
        for _, row in df.iterrows():
            holdings.append({
                'symbol':         row['代號'],
                'name':           str(row.get('股票名稱', row['代號'])).strip(),
                'shares':         float(row['目前庫存']),
                'cost_per_share': float(row['均價']),
                'currency':       'USD',
            })

        return holdings or None

    except Exception:
        return None


def _parse_summary_csv(path: Path, col_aliases: Dict) -> Optional[pd.DataFrame]:
    """Generic flexible parser for simple holdings-summary CSVs."""
    df = _read_csv(path)
    if df is None:
        return None
    try:
        cols = list(df.columns)
        result = pd.DataFrame()
        for field, aliases in col_aliases.items():
            matched = _match_col(cols, aliases)
            if matched:
                result[field] = df[matched]

        if 'symbol' not in result.columns or 'shares' not in result.columns:
            return None

        result = result.dropna(subset=['symbol', 'shares'])
        result['symbol'] = result['symbol'].astype(str).str.strip()
        result['shares'] = _clean_num(result['shares'])
        if 'cost' in result.columns:
            result['cost'] = _clean_num(result['cost'])
        if 'total_cost' in result.columns:
            result['total_cost'] = _clean_num(result['total_cost'])

        return result[result['shares'] > 0].reset_index(drop=True)

    except Exception:
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def load_tw_holdings() -> List[Dict]:
    path = TW_CSV_FILE if TW_CSV_FILE.exists() else SAMPLE_TW_CSV

    df_raw = _read_csv(path)
    if df_raw is not None:
        # Try 對帳單 format
        if _is_dazhangdan(df_raw):
            result = _parse_dazhangdan(df_raw)
            if result:
                return result

        # Fallback: try generic summary
        df = _parse_summary_csv(path, _TW_COL_ALIASES)
        if df is not None:
            holdings = []
            for _, row in df.iterrows():
                sym = str(row['symbol'])
                sym = sym.zfill(4) if sym.isdigit() else sym
                shares = int(row.get('shares', 0) or 0)
                cost   = float(row.get('cost', 0) or 0)
                total  = float(row.get('total_cost', 0) or 0)
                if cost == 0 and total > 0 and shares > 0:
                    cost = total / shares
                holdings.append({
                    'symbol': sym,
                    'name':   str(row.get('name', sym)),
                    'shares': shares,
                    'cost_per_share': cost,
                    'currency': 'TWD',
                })
            if holdings:
                return holdings

    return _sample_tw_holdings()


def load_us_holdings() -> List[Dict]:
    path = US_CSV_FILE if US_CSV_FILE.exists() else SAMPLE_US_CSV

    df_raw = _read_csv(path)
    if df_raw is not None:
        # Try 複委託庫存 format
        if _is_fuzhuotuo(df_raw):
            result = _parse_fuzhuotuo(df_raw)
            if result:
                return result

        # Fallback: try generic summary
        df = _parse_summary_csv(path, _US_COL_ALIASES)
        if df is not None:
            holdings = []
            for _, row in df.iterrows():
                sym    = str(row['symbol']).upper().strip()
                shares = float(row.get('shares', 0) or 0)
                cost   = float(row.get('cost', 0) or 0)
                total  = float(row.get('total_cost', 0) or 0)
                if cost == 0 and total > 0 and shares > 0:
                    cost = total / shares
                holdings.append({
                    'symbol': sym,
                    'name':   str(row.get('name', sym)),
                    'shares': shares,
                    'cost_per_share': cost,
                    'currency': 'USD',
                })
            if holdings:
                return holdings

    return _sample_us_holdings()


def load_pledge_config() -> Dict:
    if PLEDGE_FILE.exists():
        with open(PLEDGE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'loans': []}


def save_pledge_config(config: Dict):
    PLEDGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PLEDGE_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    try:
        from utils.gdrive import upload
        upload(PLEDGE_FILE)
    except Exception:
        pass


def load_tw_transactions() -> List[Dict]:
    """
    Load TW transaction history for running-position P&L calculations.
    Returns list of {symbol, date (str YYYY-MM-DD), share_delta, cost_flow}.
    Only available when the CSV is in 對帳單 format.
    """
    path = TW_CSV_FILE if TW_CSV_FILE.exists() else SAMPLE_TW_CSV
    df_raw = _read_csv(path)
    if df_raw is None or not _is_dazhangdan(df_raw):
        return []
    try:
        df = df_raw.copy()
        df.columns = df.columns.str.strip()
        df['股名'] = df['股名'].str.strip()
        df['代號'] = df['股名'].map(TW_NAME_TO_TICKER)
        df = df.dropna(subset=['代號'])

        df['日期'] = pd.to_datetime(df['日期'], errors='coerce')
        df['成交股數'] = _clean_num(df['成交股數'])
        df['淨收付']   = _clean_num(df['淨收付'])
        df = df.dropna(subset=['日期', '成交股數', '淨收付'])

        # Apply per-ticker date filters (same cutoffs as _parse_dazhangdan)
        frames = []
        for ticker, since in TW_INCLUDE_FROM.items():
            mask = df['代號'] == ticker
            if since:
                mask &= df['日期'] >= pd.Timestamp(since)
            chunk = df[mask]
            if not chunk.empty:
                frames.append(chunk)

        if not frames:
            return []

        filtered = pd.concat(frames, ignore_index=True)
        filtered['is_buy']      = filtered['淨收付'] < 0
        filtered['share_delta'] = filtered.apply(
            lambda r: r['成交股數'] if r['is_buy'] else -r['成交股數'], axis=1
        )
        filtered['cost_flow'] = filtered['淨收付'].abs()
        filtered.loc[~filtered['is_buy'], 'cost_flow'] *= -1

        txns = []
        for _, row in filtered.iterrows():
            txns.append({
                'symbol':      row['代號'],
                'date':        row['日期'].strftime('%Y-%m-%d'),
                'share_delta': float(row['share_delta']),
                'cost_flow':   float(row['cost_flow']),
            })
        return txns
    except Exception:
        return []


def _sample_tw_holdings() -> List[Dict]:
    return [
        {'symbol': '0050',   'name': '元大台灣50',       'shares': 1000,  'cost_per_share': 155.0, 'currency': 'TWD'},
        {'symbol': '006208', 'name': '富邦台50',          'shares': 2000,  'cost_per_share': 88.5,  'currency': 'TWD'},
        {'symbol': '00713',  'name': '元大台灣高息低波',  'shares': 3000,  'cost_per_share': 52.3,  'currency': 'TWD'},
    ]


def _sample_us_holdings() -> List[Dict]:
    return [
        {'symbol': 'QQQM', 'name': 'Invesco Nasdaq 100 ETF',         'shares': 50,  'cost_per_share': 175.0, 'currency': 'USD'},
        {'symbol': 'VT',   'name': 'Vanguard Total World Stock ETF', 'shares': 100, 'cost_per_share': 98.5,  'currency': 'USD'},
    ]
