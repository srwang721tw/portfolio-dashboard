from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np


def enrich_holdings(
    holdings: List[Dict],
    prices: Dict[str, Optional[float]],
    usd_twd: float = 32.0,
) -> List[Dict]:
    """Add current_price, market_value_twd, unrealized_pnl, pnl_pct to each holding."""
    enriched = []
    for h in holdings:
        h = dict(h)
        symbol = h["symbol"]
        price = prices.get(symbol)
        shares = h["shares"]
        cost = h["cost_per_share"]
        is_usd = h["currency"] == "USD"
        fx = usd_twd if is_usd else 1.0

        h["current_price"] = price
        if price is not None:
            market_value = shares * price
            cost_basis = shares * cost
            pnl = market_value - cost_basis
            pnl_pct = pnl / cost_basis * 100 if cost_basis > 0 else 0.0
            h["market_value"] = market_value
            h["cost_basis"] = cost_basis
            h["unrealized_pnl"] = pnl
            h["pnl_pct"] = pnl_pct
            h["market_value_twd"] = market_value * fx
            h["cost_basis_twd"] = cost_basis * fx
            h["unrealized_pnl_twd"] = pnl * fx
        else:
            cost_basis = shares * cost
            h["market_value"] = None
            h["cost_basis"] = cost_basis
            h["unrealized_pnl"] = None
            h["pnl_pct"] = None
            h["market_value_twd"] = None
            h["cost_basis_twd"] = cost_basis * fx
            h["unrealized_pnl_twd"] = None
        enriched.append(h)
    return enriched


def portfolio_summary(tw_enriched: List[Dict], us_enriched: List[Dict]) -> Dict:
    """Aggregate total values, P&L, allocation."""
    all_holdings = tw_enriched + us_enriched

    total_cost_twd = sum(h["cost_basis_twd"] for h in all_holdings)
    total_value_twd = sum(
        h["market_value_twd"] for h in all_holdings if h["market_value_twd"] is not None
    )
    total_pnl_twd = sum(
        h["unrealized_pnl_twd"] for h in all_holdings if h["unrealized_pnl_twd"] is not None
    )

    tw_value = sum(h["market_value_twd"] or 0 for h in tw_enriched)
    us_value = sum(h["market_value_twd"] or 0 for h in us_enriched)

    pnl_pct = total_pnl_twd / total_cost_twd * 100 if total_cost_twd > 0 else 0.0

    return {
        "total_cost_twd": total_cost_twd,
        "total_value_twd": total_value_twd,
        "total_pnl_twd": total_pnl_twd,
        "pnl_pct": pnl_pct,
        "tw_value_twd": tw_value,
        "us_value_twd": us_value,
        "tw_weight": tw_value / (tw_value + us_value) * 100 if (tw_value + us_value) > 0 else 0,
        "us_weight": us_value / (tw_value + us_value) * 100 if (tw_value + us_value) > 0 else 0,
    }


def compute_portfolio_history(
    tw_holdings: List[Dict],
    us_holdings: List[Dict],
    tw_price_history: Dict[str, pd.DataFrame],
    us_price_history: Dict[str, pd.DataFrame],
    usd_twd_history: pd.Series,
    days: int = 180,
) -> pd.DataFrame:
    """
    Compute daily portfolio value and unrealized P&L from historical prices.
    Returns DataFrame with columns: date, total_value_twd, total_cost_twd, total_pnl_twd, pnl_pct
    """
    frames = []

    for h in tw_holdings:
        sym = h["symbol"]
        if sym in tw_price_history and not tw_price_history[sym].empty:
            df = tw_price_history[sym].copy()
            df.columns = [sym]
            df["value"] = df[sym] * h["shares"]
            df["cost"] = h["shares"] * h["cost_per_share"]
            df["pnl"] = df["value"] - df["cost"]
            frames.append(df[["value", "cost", "pnl"]].rename(
                columns={"value": f"{sym}_val", "cost": f"{sym}_cost", "pnl": f"{sym}_pnl"}
            ))

    for h in us_holdings:
        sym = h["symbol"]
        if sym in us_price_history and not us_price_history[sym].empty:
            df = us_price_history[sym].copy()
            df.columns = [sym]
            # Align FX rate
            fx = usd_twd_history.reindex(df.index, method="ffill").fillna(32.0)
            df["value"] = df[sym] * h["shares"] * fx
            df["cost"] = h["shares"] * h["cost_per_share"] * fx
            df["pnl"] = df["value"] - df["cost"]
            frames.append(df[["value", "cost", "pnl"]].rename(
                columns={"value": f"{sym}_val", "cost": f"{sym}_cost", "pnl": f"{sym}_pnl"}
            ))

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, axis=1)
    combined = combined.dropna(how="all")

    val_cols = [c for c in combined.columns if c.endswith("_val")]
    cost_cols = [c for c in combined.columns if c.endswith("_cost")]
    pnl_cols = [c for c in combined.columns if c.endswith("_pnl")]

    result = pd.DataFrame(index=combined.index)
    result["total_value_twd"] = combined[val_cols].sum(axis=1)
    result["total_cost_twd"] = combined[cost_cols].sum(axis=1)
    result["total_pnl_twd"] = combined[pnl_cols].sum(axis=1)
    result["pnl_pct"] = result["total_pnl_twd"] / result["total_cost_twd"] * 100
    result["daily_pnl_change"] = result["total_pnl_twd"].diff()
    result.index = pd.to_datetime(result.index).normalize()  # strip tz, keep date only
    result.index.name = "date"
    result = result.sort_index().tail(days)

    return result


def compute_pledge_ratio(
    pledged_stocks: List[Dict],
    prices: Dict[str, Optional[float]],
    loan_twd: float,
    usd_twd: float = 32.0,
) -> Tuple[Optional[float], float]:
    """
    Returns (ratio_pct, pledged_value_twd).
    pledged_stocks: [{"symbol": ..., "shares": ..., "currency": "TWD"|"USD"}]
    """
    total_value = 0.0
    for ps in pledged_stocks:
        price = prices.get(ps["symbol"])
        if price is None:
            return None, 0.0
        fx = usd_twd if ps.get("currency") == "USD" else 1.0
        total_value += ps["shares"] * price * fx

    if loan_twd <= 0:
        return None, total_value

    ratio = total_value / loan_twd * 100
    return ratio, total_value
