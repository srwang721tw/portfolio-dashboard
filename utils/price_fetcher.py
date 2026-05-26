"""
Price fetcher using yfinance 1.x API.
In yfinance 1.x, yf.download() returns MultiIndex columns: ('Price', 'Ticker').
We use individual Ticker().history() calls to avoid the multi-ticker ambiguity.
"""
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

import pandas as pd
import streamlit as st
import yfinance as yf

from config.settings import TW_TICKERS, US_TICKERS, PRICE_CACHE_TTL, HISTORY_CACHE_TTL

_ALL = {**TW_TICKERS, **US_TICKERS}


def _yf(symbol: str) -> str:
    """Map internal symbol to yfinance symbol.
    Known symbols use the explicit map; numeric-only codes auto-get .TW suffix.
    """
    if symbol in _ALL:
        return _ALL[symbol]
    # Auto-detect Taiwan stocks: 4–6 digit codes (e.g. 0050, 006208, 00713)
    if symbol.isdigit() and 4 <= len(symbol) <= 6:
        return f"{symbol}.TW"
    return symbol


@st.cache_data(ttl=PRICE_CACHE_TTL, show_spinner=False)
def fetch_current_prices(symbols: Tuple[str, ...]) -> Dict[str, Optional[float]]:
    """Fetch latest close price for each symbol via individual Ticker calls."""
    prices: Dict[str, Optional[float]] = {}
    for sym in symbols:
        try:
            hist = yf.Ticker(_yf(sym)).history(period="5d")
            prices[sym] = float(hist["Close"].dropna().iloc[-1]) if not hist.empty else None
        except Exception:
            prices[sym] = None
    return prices


@st.cache_data(ttl=PRICE_CACHE_TTL, show_spinner=False)
def fetch_usd_twd_rate() -> float:
    """USD/TWD rate. Primary: Cathay Bank digital-channel buying rate. Fallback: yfinance."""
    try:
        import requests
        url = "https://www.cathaybk.com.tw/cathaybk/personal/product/deposit/currency-billboard/"
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        res    = requests.get(url, headers=headers, timeout=6)
        tables = pd.read_html(res.text)
        for tbl in tables:
            for col in tbl.columns:
                mask = tbl[col].astype(str).str.contains("數位通路優惠匯率", na=False)
                if mask.any():
                    buy_col = next(
                        (c for c in tbl.columns if "買進" in str(c) or "buy" in str(c).lower()),
                        None,
                    )
                    if buy_col is not None:
                        val = tbl.loc[mask, buy_col].iloc[0]
                        return float(str(val).replace(",", ""))
    except Exception:
        pass
    # Fallback: yfinance
    try:
        hist = yf.Ticker("USDTWD=X").history(period="5d")
        if not hist.empty:
            return float(hist["Close"].dropna().iloc[-1])
    except Exception:
        pass
    return 32.0


@st.cache_data(ttl=HISTORY_CACHE_TTL, show_spinner=False)
def fetch_historical_prices(symbol: str, days: int = 365) -> pd.DataFrame:
    try:
        hist = yf.Ticker(_yf(symbol)).history(period=f"{days}d")
        if not hist.empty:
            return hist[["Close"]].rename(columns={"Close": symbol})
    except Exception:
        pass
    return pd.DataFrame()


@st.cache_data(ttl=HISTORY_CACHE_TTL, show_spinner=False)
def fetch_usd_twd_history(days: int = 365) -> pd.Series:
    try:
        hist = yf.Ticker("USDTWD=X").history(period=f"{days}d")
        if not hist.empty:
            return hist["Close"].rename("USDTWD")
    except Exception:
        pass
    return pd.Series(dtype=float, name="USDTWD")
