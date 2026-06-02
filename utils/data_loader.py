"""
CSV loader for 國泰證券 format.

Handles two input formats automatically:
  1. 對帳單 (transaction history): 股名, 日期, 成交股數, 淨收付, ...
  2. 複委託庫存 (US holdings snapshot): 代號, 目前庫存, 均價, 庫存成本, ...
  3. Simple summary: symbol/代號, shares, cost/均價, ...  (fallback)

Public API now requires a `username` argument; all persistence goes through
utils.db (Neon PostgreSQL) instead of local CSV files.
"""

import io
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import pandas as pd

from config.settings import SAMPLE_TW_CSV, SAMPLE_US_CSV, US_TWD_COST_BASIS
import utils.db as db

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

# Stocks to include and optional cutoff date (None = all history).
# Cutoff dates can be set per-symbol to ignore transactions before a given date —
# useful if you re-buy a ticker after fully selling it and want to reset cost basis.
# By default all dates are None; users manage their own inventory by uploading only
# the transactions they want included.
TW_INCLUDE_FROM: Dict[str, Optional[str]] = {
    '0050':   None,
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


# ── Upload validation constants ───────────────────────────────────────────────
_MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024   # 10 MB
_MAX_ROWS            = 10_000
_MAX_COLUMNS         = 60
_MAX_STRING_LEN      = 500
# Allow: Chinese, alphanumeric, spaces, dots, slashes, hyphens, underscores
_SAFE_SYMBOL_RE      = re.compile(r'^[\w一-鿿\s\.\-/\(\)]+$')


def validate_csv_upload(buf: io.BytesIO, label: str) -> Tuple[bool, str]:
    """
    Validate a CSV upload buffer before parsing into the DB.
    Returns (is_valid, error_message). error_message is '' when valid.
    Checks: file size, row count, column count, parsability.
    SQL injection is already prevented by parameterized queries in db.py.
    """
    # Size check
    buf.seek(0, 2)
    size = buf.tell()
    buf.seek(0)
    if size == 0:
        return False, f"{label}：檔案是空的"
    if size > _MAX_FILE_SIZE_BYTES:
        mb = size / 1024 / 1024
        return False, f"{label}：檔案過大（{mb:.1f} MB，上限 10 MB）"

    # Parsability check
    df = _read_csv_bytes(buf)
    if df is None:
        return False, f"{label}：無法解析 CSV（請確認編碼為 UTF-8、Big5 或 CP950）"

    # Structure checks
    if len(df) == 0:
        return False, f"{label}：CSV 無任何資料列"
    if len(df) > _MAX_ROWS:
        return False, f"{label}：資料列數過多（{len(df):,} 列，上限 {_MAX_ROWS:,} 列）"
    if len(df.columns) > _MAX_COLUMNS:
        return False, f"{label}：欄位數過多（{len(df.columns)} 欄）"

    return True, ""


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _read_csv(path: Path) -> Optional[pd.DataFrame]:
    for enc in ["utf-8", "utf-8-sig", "big5", "cp950"]:
        try:
            return pd.read_csv(path, encoding=enc)
        except (UnicodeDecodeError, Exception):
            continue
    return None


def _read_csv_bytes(buf: io.BytesIO) -> Optional[pd.DataFrame]:
    """BytesIO variant of _read_csv — used when parsing uploaded file bytes in memory."""
    for enc in ["utf-8", "utf-8-sig", "big5", "cp950"]:
        try:
            buf.seek(0)
            return pd.read_csv(buf, encoding=enc)
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
    Returns aggregated holdings list or None on failure.
    """
    try:
        rows = _parse_dazhangdan_rows(df)
        if not rows:
            return None
        return _aggregate_tw_transactions(rows) or None
    except Exception:
        return None


def _parse_dazhangdan_rows(df: pd.DataFrame) -> List[Dict]:
    """
    Parse 對帳單 DataFrame into raw per-transaction rows for DB storage.
    Returns [{symbol, name, trade_date (str YYYY-MM-DD), share_delta, cost_flow}].
    These rows preserve full transaction history; net holdings are re-derived at read time.
    """
    try:
        df = df.copy()
        df.columns = df.columns.str.strip()
        df['股名'] = df['股名'].str.strip()
        df['代號'] = df['股名'].map(TW_NAME_TO_TICKER)
        df = df.dropna(subset=['代號'])

        df['日期']   = pd.to_datetime(df['日期'], errors='coerce')
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
            return []

        filtered = pd.concat(frames, ignore_index=True)
        filtered['is_buy']      = filtered['淨收付'] < 0
        filtered['share_delta'] = filtered.apply(
            lambda r: r['成交股數'] if r['is_buy'] else -r['成交股數'], axis=1
        )
        filtered['cost_flow'] = filtered['淨收付'].abs()
        filtered.loc[~filtered['is_buy'], 'cost_flow'] *= -1  # sells reduce cost basis

        name_rev = {v: k for k, v in TW_NAME_TO_TICKER.items()}
        rows = []
        for _, row in filtered.iterrows():
            rows.append({
                'symbol':      row['代號'],
                'name':        name_rev.get(row['代號'], row['代號']),
                'trade_date':  row['日期'].strftime('%Y-%m-%d'),
                'share_delta': float(row['share_delta']),
                'cost_flow':   float(row['cost_flow']),
            })
        return rows

    except Exception:
        return []


def _aggregate_tw_transactions(rows: List[Dict]) -> List[Dict]:
    """
    Aggregate raw transaction rows into net holdings.
    Input: [{symbol, name, trade_date, share_delta, cost_flow}]
    Output: [{symbol, name, shares, cost_per_share, currency}]
    """
    groups: Dict[str, Dict] = {}
    name_map: Dict[str, str] = {}
    for r in rows:
        sym = r['symbol']
        if sym not in groups:
            groups[sym] = {'share_delta': 0.0, 'cost_flow': 0.0}
        groups[sym]['share_delta'] += r['share_delta']
        groups[sym]['cost_flow']   += r['cost_flow']
        name_map[sym] = r.get('name', sym)

    holdings = []
    for sym, agg in groups.items():
        net_shares = agg['share_delta']
        net_cost   = agg['cost_flow']
        if net_shares <= 0 or net_cost <= 0:
            continue
        holdings.append({
            'symbol':         sym,
            'name':           name_map.get(sym, sym),
            'shares':         int(net_shares),
            'cost_per_share': round(net_cost / net_shares, 4),
            'currency':       'TWD',
        })
    return holdings


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

def load_tw_holdings(username: str) -> List[Dict]:
    """Load TW holdings for this user from DB (derived from transaction history)."""
    rows = db.get_tw_transactions(username)
    if rows:
        holdings = _aggregate_tw_transactions(rows)
        if holdings:
            return holdings
    return _sample_tw_holdings()


def load_us_holdings(username: str) -> List[Dict]:
    """Load US holdings snapshot for this user from DB."""
    rows = db.get_us_holdings(username)
    return rows if rows else _sample_us_holdings()


def load_pledge_config(username: str) -> Dict:
    """Load pledge loan configuration for this user from DB."""
    return db.get_pledge_config(username)


def save_pledge_config(username: str, config: Dict) -> None:
    """Persist pledge config for this user to DB."""
    db.save_pledge_config(username, config)


def load_us_cost_twd(username: str) -> float:
    """Load the actual TWD invested in US stocks for this user."""
    return db.get_user_config_num(username, 'us_twd_cost', default=float(US_TWD_COST_BASIS))


def save_us_cost_twd(username: str, amount: float) -> None:
    """Persist the US TWD cost basis for this user to DB."""
    db.set_user_config_num(username, 'us_twd_cost', amount)


def load_tw_transactions(username: str) -> List[Dict]:
    """
    Load TW transaction history for running-position P&L calculations.
    Returns [{symbol, name, trade_date, share_delta, cost_flow}].
    """
    return db.get_tw_transactions(username)


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
