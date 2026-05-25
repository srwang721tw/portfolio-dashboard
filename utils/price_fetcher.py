from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
import yfinance as yf

from config.settings import (
    TW_TICKERS, US_TICKERS, PRICE_CACHE_TTL, HISTORY_CACHE_TTL
)

_ALL_SYMBOL_MAP = {**TW_TICKERS, **US_TICKERS}


def _yf_symbol(symbol: str) -> str:
    return _ALL_SYMBOL_MAP.get(symbol, symbol)


@st.cache_data(ttl=PRICE_CACHE_TTL, show_spinner=False)
def fetch_current_prices(symbols: Tuple[str, ...]) -> Dict[str, Optional[float]]:
    """Fetch latest close prices. symbols must be a tuple for cache key hashing."""
    prices: Dict[str, Optional[float]] = {s: None for s in symbols}
    yf_map = {_yf_symbol(s): s for s in symbols}
    yf_tickers = list(yf_map.keys())

    try:
        if len(yf_tickers) == 1:
            ticker_obj = yf.Ticker(yf_tickers[0])
            hist = ticker_obj.history(period="5d")
            if not hist.empty:
                prices[yf_map[yf_tickers[0]]] = float(hist["Close"].dropna().iloc[-1])
        else:
            raw = yf.download(
                yf_tickers,
                period="5d",
                interval="1d",
                auto_adjust=True,
                progress=False,
                group_by="ticker",
            )
            for yf_t, orig in yf_map.items():
                try:
                    if len(yf_tickers) > 1:
                        close_series = raw[yf_t]["Close"] if yf_t in raw else raw["Close"]
                    else:
                        close_series = raw["Close"]
                    prices[orig] = float(close_series.dropna().iloc[-1])
                except Exception:
                    prices[orig] = None
    except Exception:
        pass

    return prices


@st.cache_data(ttl=PRICE_CACHE_TTL, show_spinner=False)
def fetch_usd_twd_rate() -> float:
    try:
        ticker = yf.Ticker("USDTWD=X")
        hist = ticker.history(period="5d")
        if not hist.empty:
            return float(hist["Close"].dropna().iloc[-1])
    except Exception:
        pass
    return 32.0


@st.cache_data(ttl=HISTORY_CACHE_TTL, show_spinner=False)
def fetch_historical_prices(symbol: str, days: int = 365) -> pd.DataFrame:
    yf_t = _yf_symbol(symbol)
    end = datetime.now()
    start = end - timedelta(days=days)
    try:
        df = yf.download(
            yf_t,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
        if not df.empty:
            close = df["Close"].rename(symbol)
            return close.to_frame()
    except Exception:
        pass
    return pd.DataFrame()


@st.cache_data(ttl=HISTORY_CACHE_TTL, show_spinner=False)
def fetch_usd_twd_history(days: int = 365) -> pd.Series:
    end = datetime.now()
    start = end - timedelta(days=days)
    try:
        df = yf.download(
            "USDTWD=X",
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
        if not df.empty:
            return df["Close"].rename("USDTWD")
    except Exception:
        pass
    return pd.Series(dtype=float, name="USDTWD")
