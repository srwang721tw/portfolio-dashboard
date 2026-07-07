"""
CSV loader for 國泰證券 format.

Handles two input formats automatically:
  1. 對帳單 (transaction history): 股名, 日期, 成交股數, 淨收付
  2. 複委託庫存 (US holdings snapshot): 代號, 目前庫存, 均價

Public API requires a `username` argument; all persistence goes through
utils.db (Neon PostgreSQL).
"""

import io
import re
from typing import List, Dict, Optional, Tuple

import pandas as pd

from config.settings import US_TWD_COST_BASIS
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
# Set a cutoff date per-symbol to ignore transactions before a given date —
# useful if you re-buy a ticker after fully selling it and want to reset cost basis.
TW_INCLUDE_FROM: Dict[str, Optional[str]] = {
    '0050':   None,
    '006208': None,
    '00713':  None,
}

# ── Upload validation constants ───────────────────────────────────────────────
_MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024   # 10 MB
_MAX_ROWS            = 10_000
_MAX_COLUMNS         = 60
# Allow: Chinese, alphanumeric, spaces, dots, slashes, hyphens, underscores
_SAFE_SYMBOL_RE      = re.compile(r'^[\w一-鿿\s\.\-/\(\)]+$')


def validate_csv_upload(buf: io.BytesIO, label: str) -> Tuple[bool, str]:
    """
    Validate a CSV upload buffer before parsing into the DB.
    Returns (is_valid, error_message). error_message is '' when valid.
    SQL injection is prevented by parameterized queries in db.py.
    """
    buf.seek(0, 2)
    size = buf.tell()
    buf.seek(0)
    if size == 0:
        return False, f"{label}：檔案是空的"
    if size > _MAX_FILE_SIZE_BYTES:
        mb = size / 1024 / 1024
        return False, f"{label}：檔案過大（{mb:.1f} MB，上限 10 MB）"

    df = _read_csv(buf)
    if df is None:
        return False, f"{label}：無法解析 CSV（請確認編碼為 UTF-8、Big5 或 CP950）"
    if len(df) == 0:
        return False, f"{label}：CSV 無任何資料列"
    if len(df) > _MAX_ROWS:
        return False, f"{label}：資料列數過多（{len(df):,} 列，上限 {_MAX_ROWS:,} 列）"
    if len(df.columns) > _MAX_COLUMNS:
        return False, f"{label}：欄位數過多（{len(df.columns)} 欄）"

    return True, ""


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _read_csv(buf: io.BytesIO) -> Optional[pd.DataFrame]:
    """Parse CSV from a BytesIO buffer, trying multiple encodings (UTF-8, Big5, CP950)."""
    for enc in ["utf-8", "utf-8-sig", "big5", "cp950"]:
        try:
            buf.seek(0)
            return pd.read_csv(buf, encoding=enc)
        except Exception:
            continue
    return None


def _clean_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(",", ""), errors="coerce")


# ── Format detectors ──────────────────────────────────────────────────────────

def _is_dazhangdan(df: pd.DataFrame) -> bool:
    """對帳單: has 股名 + 日期 + 成交股數 + 淨收付."""
    return {'股名', '日期', '成交股數', '淨收付'}.issubset(set(df.columns.str.strip()))


def _is_fuzhuotuo(df: pd.DataFrame) -> bool:
    """複委託庫存: has 代號 + 目前庫存 + 均價."""
    return {'代號', '目前庫存', '均價'}.issubset(set(df.columns.str.strip()))


# ── Format-specific parsers ───────────────────────────────────────────────────

def _parse_dazhangdan_rows(df: pd.DataFrame) -> List[Dict]:
    """
    Parse 對帳單 DataFrame into raw per-transaction rows for DB storage.
    Returns [{symbol, name, trade_date (str YYYY-MM-DD), share_delta, cost_flow}].
    Net holdings are re-derived at read time via _aggregate_tw_transactions().
    """
    try:
        df = df.copy()
        df = df.drop_duplicates()   # remove exact duplicate rows within this CSV
        df.columns = df.columns.str.strip()
        df['股名'] = df['股名'].str.strip()
        df['代號'] = df['股名'].map(TW_NAME_TO_TICKER)
        df = df.dropna(subset=['代號'])

        df['日期']   = pd.to_datetime(df['日期'], errors='coerce')
        df['成交股數'] = _clean_num(df['成交股數'])
        df['淨收付']   = _clean_num(df['淨收付'])
        df = df.dropna(subset=['日期', '成交股數', '淨收付'])

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
    Input:  [{symbol, name, trade_date, share_delta, cost_flow}]
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
    return db.get_pledge_config(username)


def save_pledge_config(username: str, config: Dict) -> None:
    db.save_pledge_config(username, config)


def load_us_cost_twd(username: str) -> float:
    return db.get_user_config_num(username, 'us_twd_cost', default=float(US_TWD_COST_BASIS))


def save_us_cost_twd(username: str, amount: float) -> None:
    db.set_user_config_num(username, 'us_twd_cost', amount)


def load_tw_transactions(username: str) -> List[Dict]:
    return db.get_tw_transactions(username)


def _sample_tw_holdings() -> List[Dict]:
    return [
        {'symbol': '0050',   'name': '元大台灣50',      'shares': 1000, 'cost_per_share': 155.0, 'currency': 'TWD'},
        {'symbol': '006208', 'name': '富邦台50',         'shares': 2000, 'cost_per_share': 88.5,  'currency': 'TWD'},
        {'symbol': '00713',  'name': '元大台灣高息低波', 'shares': 3000, 'cost_per_share': 52.3,  'currency': 'TWD'},
    ]


def _sample_us_holdings() -> List[Dict]:
    return [
        {'symbol': 'QQQM', 'name': 'Invesco Nasdaq 100 ETF',         'shares': 50,  'cost_per_share': 175.0, 'currency': 'USD'},
        {'symbol': 'VT',   'name': 'Vanguard Total World Stock ETF', 'shares': 100, 'cost_per_share': 98.5,  'currency': 'USD'},
    ]
