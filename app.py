"""
Portfolio Dashboard — personal Taiwan + US stock tracker.
All UI text is in English to keep a low profile.
"""
import json
import streamlit as st
import altair as alt
import pandas as pd
from datetime import datetime, date, timezone, timedelta

_TZ8 = timezone(timedelta(hours=8))   # UTC+8 (Asia/Taipei)

from config.settings import (
    APP_NAME, APP_ICON,
    COLOR_POSITIVE, COLOR_NEGATIVE, COLOR_NEUTRAL, COLOR_WARNING, COLOR_PURPLE,
    PLEDGE_CRITICAL, PLEDGE_WARNING, PLEDGE_SAFE,
    TW_TICKERS, US_TICKERS, TW_CSV_FILE, US_CSV_FILE,
    PLEDGE_FILE,
)
from utils.auth import (
    has_users, create_user, verify_password, verify_totp,
    is_totp_enabled, get_totp_qr_bytes, logout,
    update_profile, get_profile,
)
from utils.data_loader import (
    load_tw_holdings, load_us_holdings,
    load_pledge_config, save_pledge_config,
    load_us_cost_twd, save_us_cost_twd,
)
from utils.price_fetcher import (
    fetch_current_prices, fetch_usd_twd_rate,
    fetch_historical_prices, fetch_usd_twd_history,
)
from utils.portfolio_calc import (
    enrich_holdings, portfolio_summary,
    compute_portfolio_history, compute_pledge_ratio,
)
from utils.history_manager import save_snapshot, get_pnl_change

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Investment Dashboard", page_icon="📈",
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


def _render(chart, height=None):
    if height:
        chart = chart.properties(height=height)
    st.altair_chart(
        chart
        .configure(background="rgba(0,0,0,0)")
        .configure_view(strokeOpacity=0)
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
                    tw_txn_json: str = "[]",
                    us_cost_twd: float = 0.0) -> pd.DataFrame:
    tw_h   = json.loads(tw_json)
    us_h   = json.loads(us_json)
    tw_txn = json.loads(tw_txn_json)
    tw_ph  = {h["symbol"]: fetch_historical_prices(h["symbol"], days) for h in tw_h}
    us_ph  = {h["symbol"]: fetch_historical_prices(h["symbol"], days) for h in us_h}
    usd_h  = fetch_usd_twd_history(days)
    return compute_portfolio_history(tw_h, us_h, tw_ph, us_ph, usd_h, days,
                                     tw_transactions=tw_txn,
                                     us_cost_twd=us_cost_twd)


# ═════════════════════════════════════════════════════════════════════════════
# AUTH — login page
# ═════════════════════════════════════════════════════════════════════════════
def show_auth():
    st.markdown("<div style='max-width:440px;margin:70px auto'>", unsafe_allow_html=True)
    st.markdown(
        "<div style='text-align:center;font-size:2.5rem'>📈</div>"
        "<div style='text-align:center;font-size:1.4rem;font-weight:700;margin-bottom:20px'>"
        "Investment Dashboard</div>",
        unsafe_allow_html=True,
    )

    tab_login, tab_reg = st.tabs(["Sign In", "Create Account"])

    with tab_login:
        with st.form("login"):
            uname = st.text_input("Username")
            pw    = st.text_input("Password", type="password")
            code  = st.text_input("2FA Code", placeholder="6-digit Google Authenticator code",
                                  max_chars=6)
            ok    = st.form_submit_button("Sign In", use_container_width=True, type="primary")
        if ok:
            if not has_users():
                st.error("No accounts found. Please create one first."); return
            if not verify_password(uname, pw):
                st.error("Invalid username or password"); return
            if is_totp_enabled(uname):
                if not code:
                    st.warning("Please enter your 2FA code"); return
                if not verify_totp(uname, code):
                    st.error("Invalid or expired 2FA code"); return
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
            totp2  = st.checkbox("Enable Google Authenticator (2FA)", value=True)
            ok2    = st.form_submit_button("Create Account", use_container_width=True,
                                           type="primary")
        if ok2:
            if not uname2 or not pw2a:
                st.error("Username and password are required"); return
            if len(pw2a) < 8:
                st.error("Password must be at least 8 characters"); return
            if pw2a != pw2b:
                st.error("Passwords do not match"); return
            success, secret = create_user(uname2, pw2a, totp2, email2)
            if not success:
                st.error("Username already exists"); return
            st.success(f"Account **{uname2}** created!")
            if totp2 and secret:
                st.markdown("#### Scan QR code with Google Authenticator")
                c1, c2 = st.columns([1, 2])
                with c1:
                    st.image(get_totp_qr_bytes(uname2, secret), width=180)
                with c2:
                    st.code(secret)
                    st.caption("Scan with Google Authenticator / Authy, or enter the key manually.")
                st.warning("Complete the 2FA setup before signing in.")

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

    pie = base.mark_arc(outerRadius=120, padAngle=0.02).encode(
        tooltip=[
            alt.Tooltip(f"{label_col}:N", title=""),
            alt.Tooltip(f"{value_col}:Q", format=",.0f", title="NT$"),
            alt.Tooltip("pct:Q", format=".1f", title="%"),
        ]
    )

    # Ticker name above mid-angle, percent below
    text_ticker = base.mark_text(radius=148, dy=-7, fontSize=11,
                                  fontWeight="bold").encode(
        text=alt.Text("ticker_lbl:N"),
        color=alt.value("#E6EDF3"),
    )
    text_pct = base.mark_text(radius=148, dy=7, fontSize=10).encode(
        text=alt.Text("pct_lbl:N"),
        color=alt.value("#8B949E"),
    )

    return (
        alt.layer(pie, text_ticker, text_pct)
        .properties(height=320, title=title)
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
    """Area + line chart showing absolute unrealized P&L level over time."""
    df = series.reset_index()
    df.columns = ["date", "pnl"]
    clr = COLOR_POSITIVE if df["pnl"].iloc[-1] >= 0 else COLOR_NEGATIVE

    base = alt.Chart(df).encode(x=alt.X("date:T", title=None))
    area = base.mark_area(opacity=0.12, interpolate="monotone",
                          color=clr)
    line = base.mark_line(strokeWidth=2, interpolate="monotone",
                          color=clr).encode(
        y=alt.Y("pnl:Q", title="NT$", axis=alt.Axis(format=",.0f")),
        tooltip=[
            alt.Tooltip("date:T", format="%Y-%m-%d"),
            alt.Tooltip("pnl:Q", format="+,.0f", title="NT$"),
        ],
    )
    area = area.encode(
        y=alt.Y("pnl:Q", title="NT$", axis=alt.Axis(format=",.0f")),
    )
    zero = alt.Chart(pd.DataFrame([{"y": 0}])).mark_rule(
        color="rgba(255,255,255,0.2)"
    ).encode(y="y:Q")

    return alt.layer(area, line, zero).properties(height=220, title=title)


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
def render_dashboard():
    # ── One-time Drive sync ───────────────────────────────────────────────────
    if not st.session_state.get("_gdrive_synced"):
        with st.spinner("Syncing data..."):
            try:
                from utils.gdrive import sync_down_all
                sync_down_all()
            except Exception:
                pass
        st.session_state._gdrive_synced = True

    # ── Load US cost basis (editable, persisted in config file) ──────────────
    _us_cost_twd = load_us_cost_twd()

    # ── Load holdings ─────────────────────────────────────────────────────────
    tw_h = load_tw_holdings()
    us_h = load_us_holdings()
    all_syms = tuple(h["symbol"] for h in tw_h + us_h)

    # ── Fetch prices ──────────────────────────────────────────────────────────
    with st.spinner("Fetching live prices…"):
        prices  = fetch_current_prices(all_syms)
        usd_twd = fetch_usd_twd_rate()

    tw_e = enrich_holdings(tw_h, prices, usd_twd)
    us_e = enrich_holdings(us_h, prices, usd_twd)

    # ── Override US cost basis with actual TWD invested ───────────────────────
    # The 複委託庫存 avg_cost is in USD; the real TWD cost is stored in config.
    # Distribute it proportionally by each symbol's USD cost share.
    if _us_cost_twd > 0:
        _us_usd_total = sum(h["cost_basis"] or 0 for h in us_e)
        if _us_usd_total > 0:
            for h in us_e:
                _frac               = (h["cost_basis"] or 0) / _us_usd_total
                h["cost_basis_twd"] = _us_cost_twd * _frac
                h["unrealized_pnl_twd"] = (
                    (h["market_value_twd"] or 0) - h["cost_basis_twd"]
                )
                h["pnl_pct"] = (
                    h["unrealized_pnl_twd"] / h["cost_basis_twd"] * 100
                    if h["cost_basis_twd"] > 0 else 0.0
                )

    summary = portfolio_summary(tw_e, us_e)
    if summary["total_value_twd"] > 0:
        save_snapshot(summary["total_value_twd"], summary["total_pnl_twd"],
                      summary["pnl_pct"])

    pnl_1d  = get_pnl_change(1)
    pnl_7d  = get_pnl_change(7)
    pnl_30d = get_pnl_change(30)

    # ── Header ────────────────────────────────────────────────────────────────
    h1, h2, h3 = st.columns([5, 1.5, 1])
    with h1:
        _now8 = datetime.now(_TZ8).strftime('%Y/%m/%d %H:%M')
        st.markdown(
            "<div style='font-size:1.4rem;font-weight:700'>📈 Investment Dashboard</div>"
            f"<div style='font-size:0.78rem;color:#8B949E'>"
            f"Updated: {_now8} (UTC+8)"
            f"&nbsp;&nbsp;USD/TWD: {usd_twd:.2f} (Cathay)</div>",
            unsafe_allow_html=True)
    with h2:
        if st.button("🔄 Refresh Prices", use_container_width=True, type="secondary"):
            st.cache_data.clear()
            st.rerun()
    with h3:
        if st.button("Sign Out", use_container_width=True, type="secondary"):
            logout(); st.rerun()

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ── KPIs ──────────────────────────────────────────────────────────────────
    pnl = summary["total_pnl_twd"]
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total Value",       fmt(summary["total_value_twd"]))
    k2.metric("Unrealized P&L",    fmt(pnl),
              f"{summary['pnl_pct']:+.2f}%", delta_color=dc(pnl))
    k3.metric("Today",
              fmt(pnl_1d)  if pnl_1d  is not None else "—",
              delta_color=dc(pnl_1d  or 0))
    k4.metric("7-Day",
              fmt(pnl_7d)  if pnl_7d  is not None else "—",
              delta_color=dc(pnl_7d  or 0))
    k5.metric("30-Day",
              fmt(pnl_30d) if pnl_30d is not None else "—",
              delta_color=dc(pnl_30d or 0))

    st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)

    # ── Three tabs ────────────────────────────────────────────────────────────
    tab_dash, tab_upload, tab_account = st.tabs(
        ["📊 Dashboard", "📤 Upload", "👤 Account"])

    with tab_dash:
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
    with c1:
        st.markdown(f"**Change Password — {uname}**")
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

    with c2:
        st.markdown("**Account Info**")
        st.write(f"**Username:** {uname}")
        st.write(f"**2FA:** {'✅ Enabled' if profile.get('totp_enabled') else '❌ Disabled'}")
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
        mv = h["market_value_twd"] or 0
        cb = h["cost_basis_twd"]   or 0
        pnl = h["unrealized_pnl_twd"]
        pct = h["pnl_pct"]
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

    rows = ([_row(h, "TW") for h in tw_e] +
            [_row(h, "US") for h in us_e])

    # Subtotals
    def _subtotal_row(label, holdings_list):
        cb  = sum(h["cost_basis_twd"]          or 0 for h in holdings_list)
        mv  = sum(h["market_value_twd"]         or 0 for h in holdings_list)
        pnl = sum(h["unrealized_pnl_twd"]       or 0 for h in holdings_list)
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

    # Highlight metrics
    tw_cb = sum(h["cost_basis_twd"] or 0 for h in tw_e)
    us_cb = sum(h["cost_basis_twd"] or 0 for h in us_e)
    tw_mv = sum(h["market_value_twd"] or 0 for h in tw_e)
    us_mv = sum(h["market_value_twd"] or 0 for h in us_e)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("TW Cost Basis",  fmt(tw_cb))
    m2.metric("TW Market Value", fmt(tw_mv),
              fmtpnl(tw_mv - tw_cb), delta_color=dc(tw_mv - tw_cb))
    m3.metric("US Cost Basis (TWD)",  fmt(us_cb))
    m4.metric("US Market Value (USD)",
              fmt(sum(h["market_value"] or 0 for h in us_e), prefix="$"),
              fmtpnl(sum(h["unrealized_pnl_twd"] or 0 for h in us_e)),
              delta_color=dc(sum(h["unrealized_pnl_twd"] or 0 for h in us_e)))

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
                save_us_cost_twd(new_cost)
                st.cache_data.clear()
                st.success(f"Updated to {fmt(new_cost)}. Refreshing…")
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
    # Always use current holdings × historical price (long-term buy-and-hold):
    # pass empty transactions so compute_portfolio_history uses the simple fallback path.
    _txn_json = "[]"

    h1, h2, h3 = st.tabs(["📅 Daily (30d)", "📆 Monthly (This Year)", "📊 Annual (3Y)"])

    with h1:
        with st.spinner("Loading price history…"):
            df60 = _cached_history(tw_json, us_json, 60, _txn_json, _us_cost)
        if df60.empty:
            st.info("No data yet."); return

        # Chart A — absolute unrealized P&L level (last 30 trading days)
        pnl_level = df60["total_pnl_twd"].dropna().tail(30)
        _render(_chart_pnl_level(pnl_level, "Unrealized P&L Level (last 30 trading days)"))

        # Chart B — daily delta (day-over-day difference of the P&L level)
        daily = df60["total_pnl_twd"].dropna().diff().dropna().tail(30)
        _render(_chart_pnl_bars_daily(daily, "Daily P&L Change — day-over-day delta"))
        _stats_row(daily)

    with h2:
        with st.spinner("Loading monthly data…"):
            this_year = datetime.now(_TZ8).year
            df_yr     = _cached_history(tw_json, us_json, 365, _txn_json, _us_cost)
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
        _stats_row(monthly)

    with h3:
        with st.spinner("Loading 3-year history (first load may take ~10 s)…"):
            df3y = _cached_history(tw_json, us_json, 1095, _txn_json, _us_cost)
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
        _stats_row(annual)


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
_PLEDGE_COLS = ["Description", "Loan Amount (TWD)", "Rate (%)",
                "Start Date", "Expiry Date", "Symbol", "Shares", "Currency",
                "Current Interest (TWD)"]


def _compute_loan_interest(loan_twd: float, rate: float, start_date: str) -> float:
    """Compute accrued interest: principal × rate% × days_elapsed / 365."""
    if rate > 0 and start_date and loan_twd > 0:
        try:
            from datetime import date as _d
            start        = _d.fromisoformat(start_date)
            days_elapsed = max(0, (_d.today() - start).days)
            return loan_twd * rate / 100 * days_elapsed / 365
        except Exception:
            pass
    return 0.0


def _loans_to_df(loans) -> pd.DataFrame:
    """Expand loan list to a flat DataFrame — one row per pledged stock."""
    rows = []
    for loan in loans:
        interest = _compute_loan_interest(
            loan.get("loan_amount_twd", 0),
            loan.get("interest_rate", 0.0),
            loan.get("date", ""),
        )
        for ps in loan.get("pledged_stocks", []):
            rows.append({
                "Description":            loan.get("description", ""),
                "Loan Amount (TWD)":      int(loan.get("loan_amount_twd", 0)),
                "Rate (%)":               float(loan.get("interest_rate", 0.0)),
                "Start Date":             loan.get("date", ""),
                "Expiry Date":            loan.get("expiry_date", ""),
                "Symbol":                 ps.get("symbol", ""),
                "Shares":                 int(ps.get("shares", 0)),
                "Currency":               ps.get("currency", "TWD"),
                "Current Interest (TWD)": int(round(interest)),
            })
    return pd.DataFrame(rows, columns=_PLEDGE_COLS) if rows else pd.DataFrame(
        columns=_PLEDGE_COLS)


def _df_to_loans(df: pd.DataFrame):
    """Collapse flat DataFrame back to loan list (groups by Description)."""
    if df.empty:
        return []
    df = df.copy()
    df["Description"] = df["Description"].astype(str).str.strip()
    df = df[df["Description"] != ""]
    df = df[df["Description"] != "nan"]

    loans   = []
    loan_id = 1
    seen    = {}    # description → index in loans list

    for _, row in df.iterrows():
        desc   = str(row["Description"]).strip()
        sym    = str(row.get("Symbol", "")).strip().upper()
        shares = int(row.get("Shares", 0) or 0)
        if not desc or not sym or shares <= 0:
            continue
        if desc not in seen:
            seen[desc] = len(loans)
            loans.append({
                "id":               loan_id,
                "description":      desc,
                "pledged_stocks":   [],
                "loan_amount_twd":  float(row.get("Loan Amount (TWD)", 0) or 0),
                "interest_rate":    float(row.get("Rate (%)", 0.0) or 0.0),
                "date":             str(row.get("Start Date", "") or ""),
                "expiry_date":      str(row.get("Expiry Date", "") or ""),
            })
            loan_id += 1
        loans[seen[desc]]["pledged_stocks"].append({
            "symbol":   sym,
            "shares":   shares,
            "currency": str(row.get("Currency", "TWD") or "TWD"),
        })

    return loans


# ═════════════════════════════════════════════════════════════════════════════
# SECTION: Pledge Monitoring
# Layout:
#   1. Live monitoring (3 metric highlights + per-stock table + gauge) — always on top
#   2. Edit loan configuration in a collapsible expander below
# ═════════════════════════════════════════════════════════════════════════════
def _section_pledge(prices, usd_twd):
    loans = load_pledge_config().get("loans", [])

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
        p_prices = fetch_current_prices(p_syms) if p_syms else {}

        total_pledge_value   = 0.0
        total_loan_principal = 0.0
        total_accrued        = 0.0
        loan_data            = []   # (loan, ratio, p_value, accrued)
        any_price_missing    = False

        for loan in loans:
            ratio, p_value, accrued = compute_pledge_ratio(
                loan["pledged_stocks"], p_prices,
                loan["loan_amount_twd"], usd_twd,
                interest_rate=loan.get("interest_rate", 0.0),
                start_date=loan.get("date", ""),
            )
            if ratio is None:
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
        m1.metric("Pledged Stock Value", fmt(total_pledge_value))
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
                      "—" if any_price_missing else fmt(0))
            if any_price_missing:
                st.caption("⚠️ Some prices unavailable — ratio cannot be calculated")

        # ── Gauge chart ────────────────────────────────────────────────────────
        if overall_ratio is not None:
            _render(_chart_pledge_gauge(overall_ratio), height=100)

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
            "One row per pledged stock. Rows with the same **Description** are grouped "
            "into one loan. Use **＋** to add rows; select a row + **Delete** to remove."
        )

        ALL_SYMS = list(TW_TICKERS.keys()) + list(US_TICKERS.keys())

        edited_df = st.data_editor(
            _loans_to_df(loans),
            num_rows="dynamic",
            column_config={
                "Description":       st.column_config.TextColumn(
                    "Description", required=True, help="Loan name / label"),
                "Loan Amount (TWD)": st.column_config.NumberColumn(
                    "Loan Amount (TWD)", min_value=0, step=10000, format="%d"),
                "Rate (%)":          st.column_config.NumberColumn(
                    "Rate (%)", min_value=0.0, max_value=20.0, step=0.01, format="%.2f"),
                "Start Date":        st.column_config.TextColumn(
                    "Start Date", help="YYYY-MM-DD"),
                "Expiry Date":       st.column_config.TextColumn(
                    "Expiry Date", help="YYYY-MM-DD (leave blank if open-ended)"),
                "Symbol":            st.column_config.SelectboxColumn(
                    "Symbol", options=ALL_SYMS + [""], required=True),
                "Shares":            st.column_config.NumberColumn(
                    "Shares", min_value=0, step=100, format="%d"),
                "Currency":          st.column_config.SelectboxColumn(
                    "Currency", options=["TWD", "USD"], required=True),
                "Current Interest (TWD)": st.column_config.NumberColumn(
                    "Current Interest (TWD)",
                    help="Auto-computed from Loan × Rate × Days / 365 (read-only)"),
            },
            disabled=["Current Interest (TWD)"],
            use_container_width=True,
            hide_index=True,
            key="pledge_editor",
        )

        if st.button("💾 Save", type="primary", use_container_width=True):
            new_loans  = _df_to_loans(edited_df)
            drive_ok   = save_pledge_config({"loans": new_loans})
            if drive_ok:
                st.success("✅ Saved and synced to Google Drive")
            else:
                try:
                    from utils.gdrive import is_configured
                    if is_configured():
                        st.warning(
                            "✅ Saved locally — Google Drive upload failed. "
                            "Make sure the file exists in Drive (run setup_drive.py once)."
                        )
                    else:
                        st.success("✅ Saved locally (Google Drive not configured)")
                except Exception:
                    st.success("✅ Saved")
            st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# TAB: Upload (CSV files only)
# ═════════════════════════════════════════════════════════════════════════════
def _tab_upload():
    st.markdown("<div class='section-title'>Upload Holdings CSV</div>",
                unsafe_allow_html=True)
    uc1, uc2 = st.columns(2)

    with uc1:
        st.caption("**TW Stocks** — 國泰證券 對帳單 (transaction history) or summary CSV")
        up_tw = st.file_uploader("TW CSV", type=["csv"], key="up_tw")
        if up_tw:
            TW_CSV_FILE.write_bytes(up_tw.getvalue())
            try:
                from utils.gdrive import upload; upload(TW_CSV_FILE)
            except Exception:
                pass
            st.success("TW CSV uploaded"); st.cache_data.clear(); st.rerun()

    with uc2:
        st.caption("**US Stocks** — 複委託庫存 (holdings snapshot) or summary CSV")
        up_us = st.file_uploader("US CSV", type=["csv"], key="up_us")
        if up_us:
            US_CSV_FILE.write_bytes(up_us.getvalue())
            try:
                from utils.gdrive import upload; upload(US_CSV_FILE)
            except Exception:
                pass
            st.success("US CSV uploaded"); st.cache_data.clear(); st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════
if not st.session_state.get("authenticated"):
    show_auth()
else:
    render_dashboard()
