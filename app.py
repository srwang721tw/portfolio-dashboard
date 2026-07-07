"""
Portfolio Dashboard — personal Taiwan + US stock tracker.
All UI text is in English to keep a low profile.
"""
import json
import time
import streamlit as st
import altair as alt
import pandas as pd
from datetime import datetime, date, timezone, timedelta

_TZ8 = timezone(timedelta(hours=8))   # UTC+8 (Asia/Taipei)

import io

from config.settings import (
    APP_NAME, APP_ICON,
    COLOR_POSITIVE, COLOR_NEGATIVE, COLOR_NEUTRAL, COLOR_WARNING, COLOR_PURPLE,
    PLEDGE_CRITICAL, PLEDGE_WARNING, PLEDGE_SAFE,
    TW_TICKERS, US_TICKERS,
)
from utils.auth import (
    has_users, create_user, verify_password, logout,
    update_profile, get_profile,
)
from utils.data_loader import (
    load_tw_holdings, load_us_holdings,
    load_pledge_config, save_pledge_config,
    load_us_cost_twd, save_us_cost_twd,
    _read_csv, _is_dazhangdan, _is_fuzhuotuo,
    _parse_dazhangdan_rows, _parse_fuzhuotuo,
    validate_csv_upload,
)
import utils.db as db
from utils.price_fetcher import (
    fetch_current_prices, fetch_usd_twd_rate,
    fetch_historical_prices, fetch_usd_twd_history,
)
from utils.portfolio_calc import (
    enrich_holdings, portfolio_summary,
    compute_portfolio_history, compute_pledge_ratio,
    _compute_loan_interest,
)
from utils.history_manager import save_snapshot

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Portfolio Dashboard", page_icon="📈",
    layout="wide", initial_sidebar_state="collapsed",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
#MainMenu, footer, header { visibility: hidden; }
.stDeployButton { display: none; }

.stTabs [data-baseweb="tab-list"] {
    background: #161B22; border-radius: 10px;
    padding: 4px 6px; gap: 2px; border-bottom: none !important;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 7px; color: #8B949E;
    font-weight: 500; font-size: 0.9rem; padding: 7px 18px; border: none !important;
}
.stTabs [aria-selected="true"] { background: #0D1117 !important; color: #E6EDF3 !important; }
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] { display: none; }

div[data-testid="stMetric"] {
    background: #161B22; border: 1px solid #30363D;
    border-radius: 10px; padding: 14px 18px !important;
}
div[data-testid="stMetricLabel"] > div { font-size: 0.78rem !important; color: #8B949E !important; }
div[data-testid="stMetricValue"] > div { font-size: 1.35rem !important; }

.section-title {
    font-size: 0.85rem; font-weight: 600; color: #E6EDF3;
    margin: 16px 0 6px 0; padding-left: 8px; border-left: 3px solid #00C896;
}
.divider { border-bottom: 1px solid #30363D; margin: 8px 0 12px 0; }
</style>
""", unsafe_allow_html=True)

# ── Altair theme ──────────────────────────────────────────────────────────────
_AX = dict(labelColor="#C9D1D9", titleColor="#8B949E", gridColor="#21262D",
           domainColor="#30363D", tickColor="#30363D", labelFontSize=11, titleFontSize=11)
PALETTE = [COLOR_POSITIVE, COLOR_NEUTRAL, COLOR_PURPLE, COLOR_WARNING, COLOR_NEGATIVE,
           "#E8855A", "#A78BFA"]

# TW stock sell-side cost factor: brokerage (0.1425% × 28% discount) + ETF transaction tax (0.1%)
# Matches the notebook: factor = 1 - ((0.1425 * 0.28 + 0.1) / 100)
TW_SELL_FACTOR = 1 - ((0.1425 * 0.28 + 0.1) / 100)   # ≈ 0.99860


def _render(chart, height=None):
    if height:
        chart = chart.properties(height=height)
    st.altair_chart(
        chart
        .configure(background="rgba(0,0,0,0)")
        .configure_view(strokeOpacity=0, clip=False)
        .configure_axis(**_AX)
        .configure_legend(labelColor="#C9D1D9", titleColor="#8B949E",
                          padding=8, cornerRadius=6, strokeColor="#30363D")
        .configure_title(color="#E6EDF3", fontSize=13, fontWeight="normal"),
        use_container_width=True,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sym(symbol: str, currency: str = "TWD") -> str:
    """Display ticker: 0050 → 0050.TW, QQQM → QQQM."""
    if currency == "TWD" and symbol.isdigit():
        return f"{symbol}.TW"
    return symbol


def fmt(v, prefix="NT$") -> str:
    """Format a monetary value as a plain integer with comma separator."""
    if v is None:
        return "—"
    if v < 0:
        return f"-{prefix}{int(round(abs(v))):,}"
    return f"{prefix}{int(round(v)):,}"


def fmtpnl(v, prefix="NT$") -> str:
    """Format P&L — always show sign, plain integer."""
    if v is None:
        return "—"
    if v >= 0:
        return f"+{prefix}{int(round(v)):,}"
    return f"-{prefix}{int(round(abs(v))):,}"


def dc(v):
    return "normal" if (v or 0) >= 0 else "inverse"


# ── Cached history helper (hashable args via JSON) ────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def _cached_history(tw_json: str, us_json: str, days: int,
                    us_cost_twd: float = 0.0) -> pd.DataFrame:
    tw_h  = json.loads(tw_json)
    us_h  = json.loads(us_json)
    tw_ph = {h["symbol"]: fetch_historical_prices(h["symbol"], days) for h in tw_h}
    us_ph = {h["symbol"]: fetch_historical_prices(h["symbol"], days) for h in us_h}
    usd_h = fetch_usd_twd_history(days)
    return compute_portfolio_history(tw_h, us_h, tw_ph, us_ph, usd_h, days,
                                     us_cost_twd=us_cost_twd)


# ═════════════════════════════════════════════════════════════════════════════
# AUTH — login page
# ═════════════════════════════════════════════════════════════════════════════
def show_auth():
    st.markdown("<div style='max-width:440px;margin:70px auto'>", unsafe_allow_html=True)
    st.markdown(
        "<div style='text-align:center;font-size:2.5rem'>📈</div>"
        "<div style='text-align:center;font-size:1.4rem;font-weight:700;margin-bottom:20px'>"
        "Portfolio Dashboard</div>",
        unsafe_allow_html=True,
    )

    tab_login, tab_reg = st.tabs(["Sign In", "Create Account"])

    with tab_login:
        with st.form("login"):
            uname = st.text_input("Username")
            pw    = st.text_input("Password", type="password")
            ok    = st.form_submit_button("Sign In", use_container_width=True, type="primary")
        if ok:
            if not verify_password(uname, pw):
                st.error("Invalid username or password"); return
            st.session_state.authenticated = True
            st.session_state.username = uname
            st.rerun()

    with tab_reg:
        if not has_users():
            st.info("First-time setup — create an admin account.")
        with st.form("register"):
            uname2 = st.text_input("Username")
            email2 = st.text_input("Email (optional)")
            pw2a   = st.text_input("Password (min 8 chars)", type="password")
            pw2b   = st.text_input("Confirm password", type="password")
            ok2    = st.form_submit_button("Create Account", use_container_width=True,
                                           type="primary")
        if ok2:
            if not uname2 or not pw2a:
                st.error("Username and password are required"); return
            if len(pw2a) < 8:
                st.error("Password must be at least 8 characters"); return
            if pw2a != pw2b:
                st.error("Passwords do not match"); return
            success, _ = create_user(uname2, pw2a, email=email2)
            if not success:
                st.error("Username already exists"); return
            st.success(f"✅ Account **{uname2}** created! Please sign in.")

    st.markdown("</div>", unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════════════════════
# CHARTS
# ═════════════════════════════════════════════════════════════════════════════

def _chart_pie(df: pd.DataFrame, value_col: str, label_col: str,
               title: str, colors=None) -> alt.Chart:
    """
    Solid pie chart, sorted clockwise largest-first (12 o'clock start).
    Labels (ticker + %) rendered directly on each slice — no legend.
    Slices < 4 % get no label to avoid clutter.
    """
    total = df[value_col].sum()
    df    = df.copy()
    df["pct"]        = df[value_col] / total * 100
    df["ticker_lbl"] = df.apply(lambda r: r[label_col] if r["pct"] >= 4 else "", axis=1)
    df["pct_lbl"]    = df["pct"].apply(lambda x: f"{x:.1f}%" if x >= 4 else "")
    df = df.sort_values(value_col, ascending=False).reset_index(drop=True)
    n    = len(df)
    clrs = (colors or PALETTE)[:n]

    # `order` encoding controls pie stack order in Vega-Lite:
    # descending → largest slice first → starts at 12 o'clock clockwise.
    base = alt.Chart(df).encode(
        theta=alt.Theta(f"{value_col}:Q", stack=True),
        color=alt.Color(
            f"{label_col}:N",
            scale=alt.Scale(range=clrs, domain=df[label_col].tolist()),
            legend=None,
        ),
        order=alt.Order(f"{value_col}:Q", sort="descending"),
    )

    pie = base.mark_arc(outerRadius=105, padAngle=0.02).encode(
        tooltip=[
            alt.Tooltip(f"{label_col}:N", title=""),
            alt.Tooltip(f"{value_col}:Q", format=",.0f", title="NT$"),
            alt.Tooltip("pct:Q", format=".1f", title="%"),
        ]
    )

    # Ticker name above mid-angle, percent below
    text_ticker = base.mark_text(radius=132, dy=-7, fontSize=11,
                                  fontWeight="bold").encode(
        text=alt.Text("ticker_lbl:N"),
        color=alt.value("#E6EDF3"),
    )
    text_pct = base.mark_text(radius=132, dy=7, fontSize=10).encode(
        text=alt.Text("pct_lbl:N"),
        color=alt.value("#8B949E"),
    )

    return (
        alt.layer(pie, text_ticker, text_pct)
        .properties(height=360, title=title)
    )


def _chart_price(hist: pd.DataFrame, sym: str, cost_price=None,
                 line_color=COLOR_NEUTRAL, prefix="NT$"):
    if hist.empty:
        return None
    df   = hist.reset_index()
    df.columns = ["date", "price"]
    base = alt.Chart(df).encode(x=alt.X("date:T", title=None))
    layers = [
        base.mark_area(color=line_color, opacity=0.08).encode(
            y=alt.Y("price:Q", title=prefix,
                    axis=alt.Axis(format=",.2f"))),
        base.mark_line(color=line_color, strokeWidth=2).encode(y="price:Q"),
    ]
    if cost_price:
        layers.append(
            alt.Chart(pd.DataFrame([{"c": cost_price}])).mark_rule(
                color="#FFB74D", strokeDash=[6, 4], strokeWidth=1.5
            ).encode(y=alt.Y("c:Q", title=None),
                     tooltip=alt.Tooltip("c:Q", format=".2f", title="Cost"))
        )
    return alt.layer(*layers).properties(height=260)


def _chart_pnl_level(series: pd.Series, title: str):
    """Line + point chart showing absolute unrealized P&L level over time.
    Y-axis scales to data range (does not force zero as minimum).
    The zero reference line is only added when the data crosses the zero boundary;
    including it unconditionally would force Vega-Lite to extend the Y domain to 0,
    defeating the zero=False setting and making the trend appear flat.
    """
    df = series.reset_index()
    df.columns = ["date", "pnl"]
    clr = COLOR_POSITIVE if df["pnl"].iloc[-1] >= 0 else COLOR_NEGATIVE

    y_enc = alt.Y("pnl:Q", title="NT$",
                  scale=alt.Scale(zero=False),
                  axis=alt.Axis(format=",.0f"))
    tooltip = [
        alt.Tooltip("date:T", format="%Y-%m-%d"),
        alt.Tooltip("pnl:Q", format="+,.0f", title="NT$"),
    ]
    base   = alt.Chart(df).encode(x=alt.X("date:T", title=None))
    line   = base.mark_line(strokeWidth=2, interpolate="monotone",
                            color=clr).encode(y=y_enc, tooltip=tooltip)
    points = base.mark_point(filled=True, size=55, color=clr,
                             opacity=0.9).encode(y=y_enc, tooltip=tooltip)
    layers = [line, points]

    # Only overlay the zero baseline when it falls within the visible data range.
    # If layered unconditionally, Vega-Lite pulls the Y domain to include 0 and
    # the trend looks artificially flat.
    if df["pnl"].min() < 0 < df["pnl"].max():
        zero = alt.Chart(pd.DataFrame([{"y": 0}])).mark_rule(
            color="rgba(255,255,255,0.2)", strokeDash=[4, 4]
        ).encode(y="y:Q")
        layers.append(zero)

    return alt.layer(*layers).properties(height=220, title=title)


def _chart_pnl_bars(series: pd.Series, title: str):
    """Green/red bar chart for P&L change series."""
    df = series.reset_index()
    df.columns = ["date", "pnl"]
    df["color"] = df["pnl"].apply(lambda x: COLOR_POSITIVE if x >= 0 else COLOR_NEGATIVE)
    return (
        alt.Chart(df).mark_bar(width={"band": 0.75}).encode(
            x=alt.X("date:T", title=None),
            y=alt.Y("pnl:Q", title="NT$", axis=alt.Axis(format=",.0f")),
            color=alt.Color("color:N", scale=None, legend=None),
            tooltip=[
                alt.Tooltip("date:T", format="%Y-%m-%d"),
                alt.Tooltip("pnl:Q", format="+,.0f", title="NT$"),
            ],
        )
        + alt.Chart(pd.DataFrame([{"y": 0}])).mark_rule(
            color="rgba(255,255,255,0.2)").encode(y="y:Q")
    ).properties(height=260, title=title)


def _chart_pnl_bars_daily(series: pd.Series, title: str):
    """Green/red bar chart for daily P&L — ordinal x-axis with M/D labels, no overlap."""
    df = series.reset_index()
    df.columns = ["date", "pnl"]
    df["color"]    = df["pnl"].apply(lambda x: COLOR_POSITIVE if x >= 0 else COLOR_NEGATIVE)
    df["date_str"] = pd.to_datetime(df["date"]).dt.strftime("%-m/%-d")
    sort_order     = df["date_str"].tolist()   # preserve chronological order

    return (
        alt.Chart(df).mark_bar(width={"band": 0.65}).encode(
            x=alt.X("date_str:O", sort=sort_order, title=None,
                    axis=alt.Axis(labelAngle=-45, labelOverlap=True)),
            y=alt.Y("pnl:Q", title="NT$", axis=alt.Axis(format=",.0f")),
            color=alt.Color("color:N", scale=None, legend=None),
            tooltip=[
                alt.Tooltip("date_str:O", title="Date"),
                alt.Tooltip("pnl:Q", format="+,.0f", title="NT$"),
            ],
        )
        + alt.Chart(pd.DataFrame([{"y": 0}])).mark_rule(
            color="rgba(255,255,255,0.2)").encode(y="y:Q")
    ).properties(height=260, title=title)


def _chart_pnl_categorical(df_cat: pd.DataFrame, x_col: str, title: str):
    """Bar chart with categorical x-axis (months, years)."""
    df_cat = df_cat.copy()
    df_cat["color"] = df_cat["pnl"].apply(
        lambda x: COLOR_POSITIVE if x >= 0 else COLOR_NEGATIVE)
    return (
        alt.Chart(df_cat).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3,
                                    width={"band": 0.7}).encode(
            x=alt.X(f"{x_col}:N", sort=None, title=None,
                    axis=alt.Axis(labelAngle=-30, labelLimit=60)),
            y=alt.Y("pnl:Q", title="NT$", axis=alt.Axis(format=",.0f")),
            color=alt.Color("color:N", scale=None, legend=None),
            tooltip=[
                alt.Tooltip(f"{x_col}:N"),
                alt.Tooltip("pnl:Q", format="+,.0f", title="NT$"),
            ],
        )
        + alt.Chart(pd.DataFrame([{"y": 0}])).mark_rule(
            color="rgba(255,255,255,0.2)").encode(y="y:Q")
    ).properties(height=260, title=title)


def _chart_pledge_gauge(ratio: float):
    mx = 500
    zones = pd.DataFrame([
        {"x": 0,              "x2": PLEDGE_CRITICAL,
         "color": COLOR_NEGATIVE, "zone": f"Margin Call < {PLEDGE_CRITICAL}%"},
        {"x": PLEDGE_CRITICAL, "x2": PLEDGE_WARNING,
         "color": COLOR_WARNING,  "zone": f"Warning {PLEDGE_CRITICAL}–{PLEDGE_WARNING}%"},
        {"x": PLEDGE_WARNING,  "x2": PLEDGE_SAFE,
         "color": COLOR_NEUTRAL,  "zone": f"Watch {PLEDGE_WARNING}–{PLEDGE_SAFE}%"},
        {"x": PLEDGE_SAFE,     "x2": mx,
         "color": COLOR_POSITIVE, "zone": f"Safe ≥ {PLEDGE_SAFE}%"},
    ])
    sc     = alt.Scale(domain=[0, mx])
    val_df = pd.DataFrame([{"r": min(ratio, mx)}])
    return (
        alt.Chart(zones).mark_bar(height=44, opacity=0.45).encode(
            x=alt.X("x:Q", scale=sc, title="Maintenance Ratio %"),
            x2="x2:Q",
            color=alt.Color("color:N", scale=None, legend=None),
            tooltip="zone:N",
        )
        + alt.Chart(val_df).mark_rule(color="white", strokeWidth=3).encode(
            x=alt.X("r:Q", scale=sc))
        + alt.Chart(val_df).mark_text(dy=-36, fontSize=20, fontWeight="bold",
                                       color="white").encode(
            x=alt.X("r:Q", scale=sc),
            text=alt.Text("r:Q", format=".2f"),
        )
    ).properties(height=100)


# ═════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═════════════════════════════════════════════════════════════════════════════
def _apply_us_cost_override(us_enriched: list, us_cost_twd: float) -> None:
    """Redistribute a fixed TWD cost basis proportionally across US holdings (in-place)."""
    if us_cost_twd <= 0:
        return
    _us_usd_total = sum(h["cost_basis"] or 0 for h in us_enriched)
    if _us_usd_total <= 0:
        return
    for h in us_enriched:
        frac                    = (h["cost_basis"] or 0) / _us_usd_total
        h["cost_basis_twd"]     = us_cost_twd * frac
        h["unrealized_pnl_twd"] = (h["market_value_twd"] or 0) - h["cost_basis_twd"]
        h["pnl_pct"] = (
            h["unrealized_pnl_twd"] / h["cost_basis_twd"] * 100
            if h["cost_basis_twd"] > 0 else 0.0
        )


def render_dashboard():
    # 30-minute inactivity timeout
    _now = time.time()
    if _now - st.session_state.get("_last_activity", _now) > 1800:
        logout()
        st.info("Session expired. Please sign in again.")
        st.rerun()
    st.session_state._last_activity = _now

    username  = st.session_state.username
    _has_data = db.has_user_data(username)

    # ── Header (always visible) ───────────────────────────────────────────────
    h1, h2, h3 = st.columns([5, 1.5, 1])
    with h1:
        _now8 = datetime.now(_TZ8).strftime('%Y/%m/%d %H:%M')
        st.markdown(
            "<div style='font-size:1.4rem;font-weight:700'>📈 Portfolio Dashboard</div>"
            f"<div style='font-size:0.78rem;color:#8B949E'>Updated: {_now8} (UTC+8)</div>",
            unsafe_allow_html=True)
    with h2:
        if st.button("🔄 Refresh", use_container_width=True, type="secondary"):
            st.cache_data.clear()
            st.rerun()
    with h3:
        if st.button("Sign Out", use_container_width=True, type="secondary"):
            logout(); st.rerun()

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ── Tabs: Dashboard only shown after data is uploaded ─────────────────────
    if _has_data:
        tab_dash, tab_upload, tab_account = st.tabs(
            ["📊 Dashboard", "📤 Upload", "👤 Account"])
    else:
        tab_upload, tab_account = st.tabs(["📤 Upload", "👤 Account"])

    # ── Dashboard tab (only when data exists) ─────────────────────────────────
    if _has_data:
        with tab_dash:
            _us_cost_twd = load_us_cost_twd(username)
            tw_h = load_tw_holdings(username)
            us_h = load_us_holdings(username)
            all_syms = tuple(h["symbol"] for h in tw_h + us_h)

            with st.spinner("Fetching live prices…"):
                prices, price_dates = fetch_current_prices(all_syms)
                usd_twd = fetch_usd_twd_rate()

            tw_e = enrich_holdings(tw_h, prices, usd_twd)
            us_e = enrich_holdings(us_h, prices, usd_twd)

            # Override US cost basis with actual TWD invested
            _apply_us_cost_override(us_e, _us_cost_twd)

            summary = portfolio_summary(tw_e, us_e)

            # Adjusted totals — TW sell-cost factor applied to match Holdings table
            tw_cb      = sum(h["cost_basis_twd"]    or 0 for h in tw_e)
            tw_mv      = sum((h["market_value_twd"] or 0) * TW_SELL_FACTOR for h in tw_e)
            us_cb      = sum(h["cost_basis_twd"]    or 0 for h in us_e)
            us_mv_twd  = sum(h["market_value_twd"]  or 0 for h in us_e)
            us_mv_usd  = sum(h["market_value"]      or 0 for h in us_e)
            total_val  = tw_mv + us_mv_twd
            total_cb   = tw_cb + us_cb
            pnl        = total_val - total_cb
            pnl_pct    = pnl / total_cb * 100 if total_cb > 0 else 0.0
            us_pnl_twd = us_mv_twd - us_cb

            if total_val > 0:
                save_snapshot(username, total_val, pnl, pnl_pct)

            # Price date subtitle
            _tw_date = next(
                (price_dates.get(h["symbol"]) for h in tw_h if price_dates.get(h["symbol"])), "—")
            _us_date = next(
                (price_dates.get(h["symbol"]) for h in us_h if price_dates.get(h["symbol"])), "—")
            st.caption(
                f"TW as of {_tw_date} | US as of {_us_date} | USD/TWD: {usd_twd:.2f} (Cathay)"
            )

            # KPIs — equal height via delta=non-breaking-space on non-delta metrics
            k1, k2, k3, k4, k5, k6 = st.columns(6)
            k1.metric("Total Value",           fmt(total_val),
                      " ", delta_color="off")
            k2.metric("Unrealized P&L",        fmt(pnl),
                      f"{pnl_pct:+.2f}%", delta_color=dc(pnl))
            k3.metric("TW Cost Basis",         fmt(tw_cb),
                      " ", delta_color="off")
            k4.metric("TW Market Value",       fmt(tw_mv),
                      fmtpnl(tw_mv - tw_cb), delta_color=dc(tw_mv - tw_cb))
            k5.metric("US Cost Basis (TWD)",   fmt(us_cb),
                      " ", delta_color="off")
            k6.metric("US Market Value (USD)", fmt(us_mv_usd, prefix="$"),
                      fmtpnl(us_pnl_twd), delta_color=dc(us_pnl_twd))

            st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)

            _section_charts(tw_e, us_e, summary)
            _section_holdings(tw_e, us_e, _us_cost_twd)
            _section_pnl_history(tw_h, us_h, _us_cost_twd)
            _section_pledge(prices, usd_twd)

    with tab_upload:
        _tab_upload()

    with tab_account:
        _tab_account()


# ═════════════════════════════════════════════════════════════════════════════
# TAB: Account settings (password change, profile info)
# ═════════════════════════════════════════════════════════════════════════════
def _tab_account():
    uname   = st.session_state.username
    profile = get_profile(uname)

    st.markdown("<div class='section-title'>Account Settings</div>",
                unsafe_allow_html=True)

    c1, c2 = st.columns([1, 1])

    # ── Column 1: Change password / email ─────────────────────────────────────
    with c1:
        st.markdown(f"**Password & Email — {uname}**")
        with st.form("account_pw_form"):
            new_email = st.text_input("Email", value=profile.get("email", ""))
            new_pw    = st.text_input("New password (leave blank to keep)",
                                      type="password")
            new_pw2   = st.text_input("Confirm new password", type="password")
            if st.form_submit_button("Save Changes", type="primary",
                                     use_container_width=True):
                if new_pw and len(new_pw) < 8:
                    st.error("Password must be at least 8 characters")
                elif new_pw and new_pw != new_pw2:
                    st.error("Passwords do not match")
                else:
                    update_profile(uname,
                                   new_password=new_pw or None,
                                   new_email=new_email or None)
                    st.success("Changes saved!")
                    st.rerun()

        # ── Change username ────────────────────────────────────────────────────
        st.markdown("**Change Username**")
        with st.form("account_uname_form"):
            new_uname = st.text_input("New username",
                                      placeholder="Must be unique, min 2 chars")
            if st.form_submit_button("Change Username", type="secondary",
                                     use_container_width=True):
                new_uname = new_uname.strip()
                if len(new_uname) < 2:
                    st.error("Username must be at least 2 characters")
                elif new_uname == uname:
                    st.error("New username is the same as current")
                else:
                    try:
                        db.rename_user(uname, new_uname)
                        st.session_state.username = new_uname
                        st.success(f"Username changed to **{new_uname}**")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

    # ── Column 2: Account info ─────────────────────────────────────────────────
    with c2:
        st.markdown("**Account Info**")
        st.write(f"**Username:** {uname}")
        if profile.get("email"):
            st.write(f"**Email:** {profile['email']}")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION: Allocation Charts
# ═════════════════════════════════════════════════════════════════════════════
def _section_charts(tw_e, us_e, summary):
    all_e = tw_e + us_e

    # Allocation pie data
    alloc_rows = [
        {"ticker": _sym(h["symbol"], h["currency"]),
         "value":  h["market_value_twd"] or 0}
        for h in all_e if (h["market_value_twd"] or 0) > 0
    ]
    if not alloc_rows:
        return

    df_alloc = pd.DataFrame(alloc_rows)

    # TW/US split pie data
    df_split = pd.DataFrame({
        "market": ["TW", "US"],
        "value":  [summary["tw_value_twd"], summary["us_value_twd"]],
    })

    c1, c2 = st.columns(2)
    with c1:
        _render(_chart_pie(df_alloc, "value", "ticker",
                           "Holdings Allocation",
                           colors=PALETTE[:len(df_alloc)]))
    with c2:
        _render(_chart_pie(df_split, "value", "market",
                           "TW vs US",
                           colors=[COLOR_NEUTRAL, COLOR_PURPLE]))


# ═════════════════════════════════════════════════════════════════════════════
# SECTION: Full Holdings Table (with subtotals)
# ═════════════════════════════════════════════════════════════════════════════
def _section_holdings(tw_e, us_e, us_cost_twd: float):
    st.markdown("<div class='section-title'>Portfolio Holdings</div>",
                unsafe_allow_html=True)

    # Build rows
    all_e = tw_e + us_e

    def _row(h, market_label):
        cp = h["current_price"]
        mv_raw = h["market_value_twd"] or 0
        cb     = h["cost_basis_twd"]   or 0
        # Apply sell-cost factor for TW stocks (brokerage + ETF transaction tax)
        mv  = mv_raw * TW_SELL_FACTOR if h["currency"] == "TWD" else mv_raw
        pnl = (mv - cb) if mv else None
        pct = (pnl / cb * 100) if (pnl is not None and cb > 0) else None
        price_str = (f"{'$' if h['currency']=='USD' else 'NT$'}{cp:.2f}") if cp else "—"
        return {
            "Market":   market_label,
            "Ticker":   _sym(h["symbol"], h["currency"]),
            "Shares":   f"{h['shares']:,.0f}",
            "Cost/Sh":  f"{'$' if h['currency']=='USD' else 'NT$'}{h['cost_per_share']:.2f}",
            "Price":    price_str,
            "Cost":     fmt(cb),
            "Value":    fmt(mv) if mv else "—",
            "P&L":      fmtpnl(pnl) if pnl is not None else "—",
            "Return":   f"{pct:+.2f}%" if pct is not None else "—",
        }

    # Sort each market group by ticker symbol (ascending) before building rows.
    # Subtotal aggregations still use the original unsorted lists — order doesn't affect math.
    tw_sorted = sorted(tw_e, key=lambda h: h["symbol"])
    us_sorted = sorted(us_e, key=lambda h: h["symbol"])
    rows = ([_row(h, "TW") for h in tw_sorted] +
            [_row(h, "US") for h in us_sorted])

    # Subtotals — TW market value also gets the sell-cost factor applied
    def _subtotal_row(label, holdings_list):
        cb = sum(h["cost_basis_twd"] or 0 for h in holdings_list)
        mv = sum(
            (h["market_value_twd"] or 0) * (TW_SELL_FACTOR if h["currency"] == "TWD" else 1.0)
            for h in holdings_list
        )
        pnl = mv - cb
        pct = (pnl / cb * 100) if cb > 0 else 0
        return {
            "Market": "", "Ticker": f"**{label}**", "Shares": "",
            "Cost/Sh": "", "Price": "",
            "Cost":   fmt(cb),
            "Value":  fmt(mv),
            "P&L":    fmtpnl(pnl),
            "Return": f"{pct:+.2f}%",
        }

    rows.append(_subtotal_row("TW Total",    tw_e))
    rows.append(_subtotal_row("US Total",    us_e))
    rows.append(_subtotal_row("Grand Total", tw_e + us_e))

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ── US Cost Basis editor ───────────────────────────────────────────────────
    with st.expander("⚙️ Update US Cost Basis (TWD)", expanded=False):
        st.caption(
            "Enter the total TWD actually transferred to your overseas brokerage. "
            "This overrides the USD avg-cost × FX calculation and eliminates "
            "FX-drift noise in your P&L."
        )
        with st.form("us_cost_form"):
            new_cost = st.number_input(
                "Total TWD invested in US stocks",
                value=float(us_cost_twd),
                min_value=0.0,
                step=10_000.0,
                format="%.0f",
            )
            if st.form_submit_button("💾 Update", type="primary",
                                     use_container_width=True):
                _uname = st.session_state.username
                save_us_cost_twd(_uname, new_cost)
                st.cache_data.clear()
                st.success(f"Updated to {fmt(new_cost)} ✅")
                st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# SECTION: P&L Change History
# ═════════════════════════════════════════════════════════════════════════════
def _section_pnl_history(tw_h, us_h, us_cost_twd: float):
    st.markdown("<div class='section-title'>P&L Change History</div>",
                unsafe_allow_html=True)

    tw_json  = json.dumps(tw_h)
    us_json  = json.dumps(us_h)
    _us_cost = float(us_cost_twd)

    h1, h2, h3 = st.tabs(["📅 Daily (30d)", "📆 Monthly (This Year)", "📊 Annual (3Y)"])

    with h1:
        with st.spinner("Loading price history…"):
            df60 = _cached_history(tw_json, us_json, 60, _us_cost)
        if df60.empty:
            st.info("No data yet."); return

        # Chart A — absolute unrealized P&L level (last 30 trading days)
        pnl_level = df60["total_pnl_twd"].dropna().tail(30)
        _render(_chart_pnl_level(pnl_level, "Unrealized P&L Level (last 30 trading days)"))

        # Chart B — daily delta (day-over-day difference of the P&L level)
        daily = df60["total_pnl_twd"].dropna().diff().dropna().tail(30)
        _render(_chart_pnl_bars_daily(daily, "Daily P&L Change — day-over-day delta"))

    with h2:
        with st.spinner("Loading monthly data…"):
            this_year = datetime.now(_TZ8).year
            df_yr     = _cached_history(tw_json, us_json, 365, _us_cost)
        if df_yr.empty:
            st.info("No data yet."); return
        # Monthly P&L change = end-of-month P&L level minus previous month-end level
        monthly_all = df_yr["total_pnl_twd"].dropna().resample("ME").last().diff().dropna()
        monthly     = monthly_all[monthly_all.index.year == this_year]
        if monthly.empty:
            st.info("No monthly data for this year yet."); return
        df_m = pd.DataFrame({
            "month": monthly.index.strftime("%b"),
            "pnl":   monthly.values,
        })
        _render(_chart_pnl_categorical(df_m, "month",
                                        f"Monthly P&L Change — {this_year}"))

    with h3:
        with st.spinner("Loading 3-year history (first load may take ~10 s)…"):
            df3y = _cached_history(tw_json, us_json, 1095, _us_cost)
        if df3y.empty:
            st.info("No data yet."); return
        # Annual P&L change = end-of-year P&L level minus previous year-end level
        annual = (df3y["total_pnl_twd"].dropna()
                  .resample("YE").last().diff().dropna().tail(3))
        df_a = pd.DataFrame({
            "year": annual.index.strftime("%Y"),
            "pnl":  annual.values,
        })
        _render(_chart_pnl_categorical(df_a, "year", "Annual P&L Change (3 years)"))


def _stats_row(series: pd.Series):
    """Quick stats below a P&L bar chart."""
    pos = (series > 0).sum()
    neg = (series < 0).sum()
    s1, s2, s3, s4, s5 = st.columns(5)
    s1.metric("Best",       fmtpnl(series.max()))
    s2.metric("Worst",      fmtpnl(series.min()))
    s3.metric("Total",      fmtpnl(series.sum()))
    s4.metric("Win / Loss", f"{pos} / {neg}")
    s5.metric("Win Rate",   f"{pos / max(pos + neg, 1) * 100:.0f}%")


# ─────────────────────────────────────────────────────────────────────────────
# Pledge helpers
# ─────────────────────────────────────────────────────────────────────────────
# Columns shown in the edit table (Description + Currency removed).
# Each row = one pledged stock / loan entry.
_PLEDGE_COLS = [
    "Loan Amount (TWD)", "Rate (%)", "Start Date", "Expiry Date",
    "Symbol", "Shares", "Current Interest (TWD)",
]

_TW_PLEDGE_SYMS = list(TW_TICKERS.keys())   # only TW stocks can be pledged


def _parse_date(s):
    """String → datetime.date; None if blank/invalid."""
    from datetime import date as _d
    if not s or str(s).strip().lower() in ("", "none", "nat", "nan", "pd.nat"):
        return None
    try:
        return _d.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _date_to_str(v) -> str:
    """datetime.date / Timestamp / string → 'YYYY-MM-DD'; '' if empty."""
    if v is None:
        return ""
    try:
        if isinstance(v, float) and pd.isna(v):
            return ""
        if hasattr(v, "strftime"):
            return v.strftime("%Y-%m-%d")
        s = str(v)[:10]
        return s if len(s) == 10 else ""
    except Exception:
        return ""


def _loans_to_df(loans) -> pd.DataFrame:
    """
    Expand loan list → flat DataFrame, one row per pledged stock.
    Current Interest shows the stored manual override value; if none was saved,
    falls back to the auto-computed amount so the user has a sensible default.
    Dates are returned as datetime.date objects so Streamlit shows a date picker.
    """
    rows = []
    for loan in loans:
        # Prefer stored manual interest; fall back to auto-computed default
        stored_interest = loan.get("override_interest_twd")
        if stored_interest is not None:
            interest = float(stored_interest)
        else:
            interest = _compute_loan_interest(
                loan.get("loan_amount_twd", 0),
                loan.get("interest_rate", 0.0),
                loan.get("date", ""),
            )
        for ps in loan.get("pledged_stocks", []):
            rows.append({
                "Loan Amount (TWD)":      int(loan.get("loan_amount_twd", 0)),
                "Rate (%)":               float(loan.get("interest_rate", 0.0)),
                "Start Date":             _parse_date(loan.get("date", "")),
                "Expiry Date":            _parse_date(loan.get("expiry_date", "")),
                "Symbol":                 ps.get("symbol", ""),
                "Shares":                 int(ps.get("shares", 0)),
                "Current Interest (TWD)": int(round(interest)),
            })
    return (
        pd.DataFrame(rows, columns=_PLEDGE_COLS) if rows
        else pd.DataFrame(columns=_PLEDGE_COLS)
    )


def _df_to_loans(df: pd.DataFrame):
    """
    Convert flat DataFrame back to loan list.
    Each row becomes its own loan (one pledged stock per loan).
    Currency is always TWD (TW stocks only).
    """
    if df.empty:
        return []
    loans = []
    for i, row in df.iterrows():
        sym    = str(row.get("Symbol", "")).strip().upper()
        shares = int(row.get("Shares", 0) or 0)
        if not sym or shares <= 0:
            continue
        loans.append({
            "id":                    len(loans) + 1,
            "description":           f"{sym} Pledge {len(loans) + 1}",
            "pledged_stocks":        [{"symbol": sym, "shares": shares,
                                       "currency": "TWD"}],
            "loan_amount_twd":       float(row.get("Loan Amount (TWD)", 0) or 0),
            "interest_rate":         float(row.get("Rate (%)", 0.0) or 0.0),
            "date":                  _date_to_str(row.get("Start Date")),
            "expiry_date":           _date_to_str(row.get("Expiry Date")),
            "override_interest_twd": float(row.get("Current Interest (TWD)", 0) or 0),
        })
    return loans


# ═════════════════════════════════════════════════════════════════════════════
# SECTION: Pledge Monitoring
# Layout:
#   1. Live monitoring (3 metric highlights + per-stock table + gauge) — always on top
#   2. Edit loan configuration in a collapsible expander below
# ═════════════════════════════════════════════════════════════════════════════
def _section_pledge(prices, usd_twd):
    _uname = st.session_state.username
    loans = load_pledge_config(_uname).get("loans", [])

    st.markdown("<div class='section-title'>Pledge Monitoring</div>",
                unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════════════
    # Part 1 — LIVE MONITORING (read-only, computed from current prices)
    # ════════════════════════════════════════════════════════════════════════
    if not loans:
        st.info("No pledge loans configured yet. Expand the editor below to add loans.")
    else:
        p_syms   = tuple({s["symbol"] for loan in loans
                          for s in loan.get("pledged_stocks", [])})
        p_prices, _ = fetch_current_prices(p_syms) if p_syms else ({}, {})

        total_pledge_value   = 0.0
        total_loan_principal = 0.0
        total_accrued        = 0.0
        loan_data            = []   # (loan, ratio, p_value, accrued)
        any_price_missing    = False

        for loan in loans:
            stored = loan.get("override_interest_twd")
            ratio, p_value, accrued = compute_pledge_ratio(
                loan["pledged_stocks"], p_prices,
                loan["loan_amount_twd"], usd_twd,
                interest_rate=loan.get("interest_rate", 0.0),
                start_date=loan.get("date", ""),
                override_accrued=float(stored) if stored is not None else None,
            )
            # ratio is None for two distinct reasons:
            #   1. Price unavailable  → compute_pledge_ratio returns (None, 0.0, 0.0)
            #   2. Zero-loan pledge   → returns (None, total_value, accrued)
            #      (stocks pledged without borrowing, used to boost overall ratio)
            # Only set any_price_missing for case 1 (p_value stays 0 when price fetch fails).
            if ratio is None and p_value == 0.0 and loan.get("pledged_stocks"):
                any_price_missing = True
            total_pledge_value   += p_value or 0
            total_loan_principal += loan["loan_amount_twd"]
            total_accrued        += accrued
            loan_data.append((loan, ratio, p_value, accrued))

        total_liability = total_loan_principal + total_accrued
        overall_ratio   = (
            total_pledge_value / total_liability * 100
            if (total_liability > 0 and not any_price_missing) else None
        )

        # ── Highlight 1: Pledged Stock Value ──────────────────────────────────
        # ── Highlight 2: Total Loan Amount (incl. interest) ───────────────────
        # ── Highlight 3: Overall Maintenance Ratio ────────────────────────────
        m1, m2, m3 = st.columns(3)
        # All three metrics get a delta row so card heights are equal
        m1.metric("Pledged Stock Value", fmt(total_pledge_value),
                  "\xa0", delta_color="off")
        m2.metric(
            "Total Loan Amount (incl. Interest)",
            fmt(total_liability),
            f"Interest accrued: {fmt(total_accrued)}",
            delta_color="off",
        )
        if overall_ratio is not None:
            if overall_ratio < PLEDGE_CRITICAL:
                r_delta, r_dc = "🔴 MARGIN CALL", "inverse"
            elif overall_ratio < PLEDGE_WARNING:
                r_delta, r_dc = "🟠 Warning",      "inverse"
            elif overall_ratio < PLEDGE_SAFE:
                r_delta, r_dc = "🟡 Watch",         "off"
            else:
                r_delta, r_dc = "🟢 Safe",          "normal"
            m3.metric("Overall Maintenance Ratio", f"{overall_ratio:.2f}%",
                      r_delta, delta_color=r_dc)
        else:
            m3.metric("Overall Maintenance Ratio",
                      "—" if any_price_missing else fmt(0),
                      "\xa0", delta_color="off")
            if any_price_missing:
                st.caption("⚠️ Some prices unavailable — ratio cannot be calculated")

        # ── Per-stock monitoring table ─────────────────────────────────────────
        # Columns: 日期 | 到期日 | 股票代號 | 股數 | 利率 | 借款金額 | 目前利息 | 維持率
        rows = []
        for loan, ratio, p_value, accrued in loan_data:
            for ps in loan.get("pledged_stocks", []):
                sym      = ps["symbol"]
                shares   = ps.get("shares", 0)
                currency = ps.get("currency", "TWD")
                rows.append({
                    "Date":            loan.get("date", ""),
                    "Expiry Date":     loan.get("expiry_date", "") or "—",
                    "Symbol":          _sym(sym, currency),
                    "Shares":          f"{shares:,}",
                    "Rate (%)":        f"{loan.get('interest_rate', 0):.2f}%",
                    "Loan Amount":     fmt(loan["loan_amount_twd"]),
                    "Current Interest":fmt(accrued) if accrued > 0.5 else "NT$0",
                    "Maint. Ratio":    f"{ratio:.2f}%" if ratio is not None else "—",
                })
        if rows:
            st.markdown("<div style='margin-top:8px'></div>", unsafe_allow_html=True)
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ════════════════════════════════════════════════════════════════════════
    # Part 2 — EDIT LOAN CONFIGURATION (collapsible)
    # Expanded by default when no loans exist, collapsed otherwise.
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
    with st.expander("✏️ Edit Loan Configuration", expanded=(not loans)):
        st.caption(
            "One row per pledged stock. Use **＋** to add rows; "
            "select a row + **Delete** to remove. "
            "Click any date cell to open a calendar picker."
        )

        from datetime import date as _dt_today
        edited_df = st.data_editor(
            _loans_to_df(loans),
            num_rows="dynamic",
            column_config={
                "Loan Amount (TWD)": st.column_config.NumberColumn(
                    "Loan Amount (TWD)", min_value=0, step=10_000, format="%d"),
                "Rate (%)":          st.column_config.NumberColumn(
                    "Rate (%)", min_value=0.0, max_value=20.0,
                    step=0.01, format="%.2f"),
                "Start Date":        st.column_config.DateColumn(
                    "Start Date",
                    format="YYYY-MM-DD",
                    help="Click to pick a date"),
                "Expiry Date":       st.column_config.DateColumn(
                    "Expiry Date",
                    format="YYYY-MM-DD",
                    help="Leave blank if open-ended"),
                "Symbol":            st.column_config.SelectboxColumn(
                    "Symbol", options=_TW_PLEDGE_SYMS + [""], required=True),
                "Shares":            st.column_config.NumberColumn(
                    "Shares", min_value=0, step=100, format="%d"),
                "Current Interest (TWD)": st.column_config.NumberColumn(
                    "Current Interest (TWD)", min_value=0, step=100, format="%d",
                    help="Enter the actual accrued interest amount (TWD)"),
            },
            use_container_width=True,
            hide_index=True,
            key="pledge_editor",
        )

        if st.button("💾 Save", type="primary", use_container_width=True):
            new_loans = _df_to_loans(edited_df)
            save_pledge_config(_uname, {"loans": new_loans})
            st.success("✅ Saved")
            st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# TAB: Upload (CSV files only)
# ═════════════════════════════════════════════════════════════════════════════
def _tab_upload():
    st.markdown("<div class='section-title'>Upload Holdings CSV</div>",
                unsafe_allow_html=True)
    username = st.session_state.username
    uc1, uc2 = st.columns(2)

    with uc1:
        st.caption(
            "**TW Stocks** — 國泰證券 對帳單（可同時上傳多個 CSV，自動合併去重後寫入）"
        )
        up_tw_files = st.file_uploader(
            "TW CSV", type=["csv"], key="up_tw", accept_multiple_files=True
        )
        if up_tw_files:
            all_rows = []
            errors   = []
            for f in up_tw_files:
                buf  = io.BytesIO(f.getvalue())
                ok, err = validate_csv_upload(buf, f.name)
                if not ok:
                    errors.append(err); continue
                buf.seek(0)
                df = _read_csv(buf)
                if df is None or not _is_dazhangdan(df):
                    errors.append(
                        f"{f.name}：格式不符（需含欄位：股名、日期、成交股數、淨收付）"
                    )
                    continue
                rows = _parse_dazhangdan_rows(df)
                if not rows:
                    errors.append(f"{f.name}：無符合條件的交易資料")
                    continue
                all_rows.extend(rows)

            if errors:
                for e in errors:
                    st.error(e)

            if all_rows:
                # Only dedup when multiple files are uploaded — single file data
                # is trusted as-is to avoid dropping legitimately identical transactions
                # (e.g. two separate buy orders on the same day at the same price).
                if len(up_tw_files) > 1:
                    seen, deduped = set(), []
                    for r in all_rows:
                        key = (r["symbol"], r["trade_date"],
                               round(r["share_delta"], 4), round(r["cost_flow"], 4))
                        if key not in seen:
                            seen.add(key)
                            deduped.append(r)
                else:
                    deduped = all_rows
                db.replace_tw_transactions(username, deduped)
                st.cache_data.clear()
                st.success(
                    f"✅ 已匯入 {len(deduped)} 筆交易紀錄"
                    + (f"（來自 {len(up_tw_files)} 個檔案，去重後）" if len(up_tw_files) > 1 else "")
                )
                st.rerun()

    with uc2:
        st.caption(
            "**US Stocks** — 複委託庫存（可同時上傳多個 CSV，相同代號以最後一個檔案為準）"
        )
        up_us_files = st.file_uploader(
            "US CSV", type=["csv"], key="up_us", accept_multiple_files=True
        )
        if up_us_files:
            merged: dict = {}   # symbol → holding dict (last file wins)
            errors  = []
            for f in up_us_files:
                buf  = io.BytesIO(f.getvalue())
                ok, err = validate_csv_upload(buf, f.name)
                if not ok:
                    errors.append(err); continue
                buf.seek(0)
                df = _read_csv(buf)
                if df is None or not _is_fuzhuotuo(df):
                    errors.append(
                        f"{f.name}：格式不符（需含欄位：代號、目前庫存、均價）"
                    )
                    continue
                holdings = _parse_fuzhuotuo(df) or []
                if not holdings:
                    errors.append(f"{f.name}：無有效庫存資料（庫存數量需 > 0）")
                    continue
                for h in holdings:
                    merged[h["symbol"]] = h   # last file wins for duplicate symbols

            if errors:
                for e in errors:
                    st.error(e)

            if merged:
                all_holdings = list(merged.values())
                db.replace_us_holdings(username, all_holdings)

                # Auto-compute us_twd_cost from CSV: shares × avg_cost × FX rate
                usd_twd = fetch_usd_twd_rate()
                auto_cost = sum(
                    h["shares"] * h["cost_per_share"] for h in all_holdings
                ) * usd_twd
                if auto_cost > 0:
                    save_us_cost_twd(username, round(auto_cost, 2))

                st.cache_data.clear()
                st.success(
                    f"✅ 已匯入 {len(all_holdings)} 筆美股庫存"
                    + (f"（來自 {len(up_us_files)} 個檔案）" if len(up_us_files) > 1 else "")
                )
                if auto_cost > 0:
                    st.caption(
                        f"US Cost Basis 已自動設為 NT${auto_cost:,.0f}（均價 × 股數 × {usd_twd:.2f}），"
                        "如有需要可至 Dashboard → Holdings 手動修改。"
                    )
                st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

# Ensure DB schema exists on startup (idempotent; fast after first call).
try:
    db.ensure_schema()
except Exception:
    st.error("Database unavailable. Please try again in a moment.")
    st.stop()

if not st.session_state.get("authenticated"):
    show_auth()
else:
    render_dashboard()
