from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np


def _tw_running_frame(sym: str, price_history: pd.DataFrame,
                      txn_df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """
    Compute per-symbol TW P&L using the actual running position from transactions.

    At each price date we use:
      - shares_at_date  = cumulative sum of share_delta up to that date
      - cost_at_date    = cumulative sum of cost_flow  up to that date
      - pnl_at_date     = shares_at_date * price - cost_at_date

    Returns a DataFrame indexed by price dates with columns
    {sym}_val, {sym}_cost, {sym}_pnl — or None if no transactions for this symbol.
    """
    sym_txns = txn_df[txn_df['symbol'] == sym].copy()
    if sym_txns.empty:
        return None

    sym_txns['date'] = pd.to_datetime(sym_txns['date']).dt.normalize()

    # Daily cumulative position
    daily = sym_txns.groupby('date').agg({'share_delta': 'sum', 'cost_flow': 'sum'})
    cum   = daily.cumsum()

    # Clean price series
    prices = price_history.iloc[:, 0].copy()
    prices.index = pd.to_datetime(prices.index).normalize()
    prices = prices[~prices.index.duplicated(keep='last')].sort_index()

    if prices.empty or cum.empty:
        return None
    if cum.index.min() > prices.index.max():
        return None  # all transactions are in the future relative to price window

    # Build a full daily index so forward-fill works across weekends/holidays
    start    = min(cum.index.min(), prices.index.min())
    full_idx = pd.date_range(start=start, end=prices.index.max(), freq='D')

    cum_full     = cum.reindex(full_idx).ffill().fillna(0)
    cum_at_price = cum_full.reindex(prices.index).ffill().fillna(0)

    mask = cum_at_price['share_delta'] > 0
    if not mask.any():
        return None

    result = pd.DataFrame(index=prices.index[mask])
    result[f'{sym}_val']  = (prices[mask].values
                              * cum_at_price.loc[mask, 'share_delta'].values)
    result[f'{sym}_cost'] = cum_at_price.loc[mask, 'cost_flow'].values
    result[f'{sym}_pnl']  = result[f'{sym}_val'] - result[f'{sym}_cost']
    return result


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
    tw_transactions: Optional[List[Dict]] = None,
) -> pd.DataFrame:
    """
    Compute daily portfolio value and unrealized P&L from historical prices.

    When tw_transactions is provided (list of {symbol, date, share_delta, cost_flow}),
    TW stocks use the actual running position at each historical date instead of the
    current shares/cost.  US stocks always use the current snapshot (no tx history).

    Returns DataFrame with columns: date, total_value_twd, total_cost_twd, total_pnl_twd,
    pnl_pct, daily_pnl_change
    """
    frames = []

    # Build transaction DataFrame once (if available)
    txn_df: Optional[pd.DataFrame] = None
    if tw_transactions:
        txn_df = pd.DataFrame(tw_transactions)
        if txn_df.empty or 'symbol' not in txn_df.columns:
            txn_df = None

    for h in tw_holdings:
        sym = h["symbol"]
        if sym not in tw_price_history or tw_price_history[sym].empty:
            continue

        # ── Transaction-based running position ──────────────────────────────
        if txn_df is not None:
            frame = _tw_running_frame(sym, tw_price_history[sym], txn_df)
            if frame is not None:
                frames.append(frame)
                continue

        # ── Fallback: current holdings × historical price ────────────────────
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
