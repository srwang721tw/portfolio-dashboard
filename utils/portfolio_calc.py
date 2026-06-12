from typing import Dict, List, Optional, Tuple
from datetime import date as _date
import pandas as pd


def _strip_tz(index) -> pd.DatetimeIndex:
    """Normalize a DatetimeIndex to tz-naive date-only (strips time and timezone)."""
    idx = pd.to_datetime(index).normalize()
    return idx.tz_localize(None) if idx.tz is not None else idx


def _compute_loan_interest(
    principal: float,
    rate_pct: float,
    start_date: str,
    override: Optional[float] = None,
) -> float:
    """
    Return accrued interest in TWD.
    If override is set, return it directly.
    Otherwise compute: principal × rate% × days_elapsed / 365.
    """
    if override is not None:
        return float(override)
    if rate_pct > 0 and start_date and principal > 0:
        try:
            start        = _date.fromisoformat(start_date)
            days_elapsed = max(0, (_date.today() - start).days)
            return principal * rate_pct / 100 * days_elapsed / 365
        except Exception:
            pass
    return 0.0


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
    us_cost_twd: float = 0.0,
) -> pd.DataFrame:
    """
    Compute daily portfolio value and unrealized P&L from historical prices.

    Uses current holdings × historical price for all symbols.

    us_cost_twd: if > 0, use this fixed TWD amount as the total US cost basis
                 (the actual TWD wired to broker), distributed proportionally by USD cost.
                 This prevents FX fluctuations from distorting the US cost line.

    Returns DataFrame with columns: date, total_value_twd, total_cost_twd, total_pnl_twd,
    pnl_pct, daily_pnl_change
    """
    frames = []

    for h in tw_holdings:
        sym = h["symbol"]
        if sym not in tw_price_history or tw_price_history[sym].empty:
            continue
        df = tw_price_history[sym].copy()
        df.index = _strip_tz(df.index)
        df.columns = [sym]
        df["value"] = df[sym] * h["shares"]
        df["cost"] = h["shares"] * h["cost_per_share"]
        df["pnl"] = df["value"] - df["cost"]
        frames.append(df[["value", "cost", "pnl"]].rename(
            columns={"value": f"{sym}_val", "cost": f"{sym}_cost", "pnl": f"{sym}_pnl"}
        ))

    # Pre-compute FX series once (tz-stripped)
    usd_idx = usd_twd_history.copy()
    usd_idx.index = _strip_tz(usd_idx.index)

    _us_usd_total = sum(h["shares"] * h["cost_per_share"] for h in us_holdings)

    for h in us_holdings:
        sym = h["symbol"]
        if sym not in us_price_history or us_price_history[sym].empty:
            continue
        df = us_price_history[sym].copy()
        df.index = _strip_tz(df.index)
        df.columns = [sym]

        fx = usd_idx.reindex(df.index, method="ffill").fillna(32.0)
        df["value"] = df[sym] * h["shares"] * fx

        if us_cost_twd > 0 and _us_usd_total > 0:
            frac       = (h["shares"] * h["cost_per_share"]) / _us_usd_total
            df["cost"] = us_cost_twd * frac
        else:
            df["cost"] = h["shares"] * h["cost_per_share"] * fx

        df["pnl"] = df["value"] - df["cost"]
        frames.append(df[["value", "cost", "pnl"]].rename(
            columns={"value": f"{sym}_val", "cost": f"{sym}_cost", "pnl": f"{sym}_pnl"}
        ))

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, axis=1)
    # Forward-fill so that market-closure days carry the previous close instead
    # of NaN — prevents those days from being omitted from the portfolio total.
    combined = combined.ffill()
    combined = combined.dropna(how="all")

    val_cols  = [c for c in combined.columns if c.endswith("_val")]
    cost_cols = [c for c in combined.columns if c.endswith("_cost")]
    pnl_cols  = [c for c in combined.columns if c.endswith("_pnl")]

    result = pd.DataFrame(index=combined.index)
    result["total_value_twd"]  = combined[val_cols].sum(axis=1)
    result["total_cost_twd"]   = combined[cost_cols].sum(axis=1)
    result["total_pnl_twd"]    = combined[pnl_cols].sum(axis=1)
    result["pnl_pct"]          = result["total_pnl_twd"] / result["total_cost_twd"] * 100
    result["daily_pnl_change"] = result["total_pnl_twd"].diff()
    result.index = _strip_tz(result.index)
    result.index.name = "date"
    result = result.sort_index().tail(days)

    return result


def compute_pledge_ratio(
    pledged_stocks: List[Dict],
    prices: Dict[str, Optional[float]],
    loan_twd: float,
    usd_twd: float = 32.0,
    interest_rate: float = 0.0,
    start_date: str = "",
    override_accrued: Optional[float] = None,
) -> Tuple[Optional[float], float, float]:
    """
    Returns (ratio_pct, pledged_value_twd, accrued_interest_twd).

    Maintenance ratio = pledged_value / (principal + accrued_interest) × 100%.
    override_accrued: if provided, use this directly as the interest amount
                      instead of computing from rate/days (manual input mode).
    pledged_stocks: [{"symbol": ..., "shares": ..., "currency": "TWD"|"USD"}]
    """
    total_value = 0.0
    for ps in pledged_stocks:
        price = prices.get(ps["symbol"])
        if price is None:
            return None, 0.0, 0.0
        fx = usd_twd if ps.get("currency") == "USD" else 1.0
        total_value += ps["shares"] * price * fx

    accrued       = _compute_loan_interest(loan_twd, interest_rate, start_date, override_accrued)
    total_liability = loan_twd + accrued
    if total_liability <= 0:
        return None, total_value, accrued

    ratio = total_value / total_liability * 100
    return ratio, total_value, accrued
