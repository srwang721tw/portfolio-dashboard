"""
Portfolio Dashboard — personal Taiwan + US stock tracker.
All UI text is in English to keep a low profile.
"""
import json
import streamlit as st
import altair as alt
import pandas as pd
from datetime import datetime, date

from config.settings import (
    APP_NAME, APP_ICON,
    COLOR_POSITIVE, COLOR_NEGATIVE, COLOR_NEUTRAL, COLOR_WARNING, COLOR_PURPLE,
    PLEDGE_CRITICAL, PLEDGE_WARNING, PLEDGE_SAFE,
    TW_TICKERS, US_TICKERS, TW_CSV_FILE, US_CSV_FILE,
)
from utils.auth import (
    has_users, create_user, verify_password, verify_totp,
    is_totp_enabled, get_totp_qr_bytes, logout,
    update_profile, get_profile,
)
from utils.data_loader import (
    load_tw_holdings, load_us_holdings,
    load_pledge_config, save_pledge_config,
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
    if v is None:
        return "—"
    av = abs(v)
    if av >= 1_000_000:
        return f"{prefix}{v/1_000_000:+.2f}M" if v < 0 else f"{prefix}{v/1_000_000:.2f}M"
    if av >= 1_000:
        return f"{prefix}{v/1_000:+.1f}K" if v < 0 else f"{prefix}{v/1_000:.1f}K"
    return f"{prefix}{v:,.0f}"


def fmtpnl(v, prefix="NT$") -> str:
    """Always show sign for P&L values."""
    if v is None:
        return "—"
    av = abs(v)
    if av >= 1_000_000:
        return f"{'+' if v>=0 else ''}{prefix}{v/1_000_000:.2f}M"
    if av >= 1_000:
        return f"{'+' if v>=0 else ''}{prefix}{v/1_000:.1f}K"
    return f"{'+' if v>=0 else ''}{prefix}{v:,.0f}"


def dc(v):
    return "normal" if (v or 0) >= 0 else "inverse"


# ── Cached history helper (hashable args via JSON) ────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def _cached_history(tw_json: str, us_json: str, days: int) -> pd.DataFrame:
    tw_h = json.loads(tw_json)
    us_h = json.loads(us_json)
    tw_ph = {h["symbol"]: fetch_historical_prices(h["symbol"], days) for h in tw_h}
    us_ph = {h["symbol"]: fetch_historical_prices(h["symbol"], days) for h in us_h}
    usd_h = fetch_usd_twd_history(days)
    return compute_portfolio_history(tw_h, us_h, tw_ph, us_ph, usd_h, days)


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
    """Solid pie chart, sorted clockwise largest-first, with % tooltip."""
    total   = df[value_col].sum()
    df      = df.copy()
    df["pct"] = df[value_col] / total * 100
    df      = df.sort_values(value_col, ascending=False).reset_index(drop=True)
    n       = len(df)
    clrs    = (colors or PALETTE)[:n]
    return (
        alt.Chart(df).mark_arc(padAngle=0.025).encode(
            theta=alt.Theta(f"{value_col}:Q", stack=True),
            color=alt.Color(f"{label_col}:N",
                            scale=alt.Scale(range=clrs),
                            sort=df[label_col].tolist(),
                            legend=alt.Legend(title=None, orient="bottom",
                                              columns=3, labelLimit=120)),
            tooltip=[
                alt.Tooltip(f"{label_col}:N", title=""),
                alt.Tooltip(f"{value_col}:Q", format=",.0f", title="NT$"),
                alt.Tooltip("pct:Q", format=".1f", title="%"),
            ],
        ).properties(height=240, title=title)
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
                    axis=alt.Axis(format=".3~s"))),
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


def _chart_pnl_bars(series: pd.Series, title: str):
    """Green/red bar chart for P&L change series."""
    df = series.reset_index()
    df.columns = ["date", "pnl"]
    df["color"] = df["pnl"].apply(lambda x: COLOR_POSITIVE if x >= 0 else COLOR_NEGATIVE)
    return (
        alt.Chart(df).mark_bar(width={"band": 0.75}).encode(
            x=alt.X("date:T", title=None),
            y=alt.Y("pnl:Q", title="NT$", axis=alt.Axis(format=".3~s")),
            color=alt.Color("color:N", scale=None, legend=None),
            tooltip=[
                alt.Tooltip("date:T", format="%Y-%m-%d"),
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
            y=alt.Y("pnl:Q", title="NT$", axis=alt.Axis(format=".3~s")),
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
            text=alt.Text("r:Q", format=".1f"),
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

    # ── Load holdings ─────────────────────────────────────────────────────────
    tw_h = load_tw_holdings()
    us_h = load_us_holdings()
    all_syms = tuple(h["symbol"] for h in tw_h + us_h)

    # ── Fetch prices (with spinner) ───────────────────────────────────────────
    with st.spinner("Fetching live prices…"):
        prices  = fetch_current_prices(all_syms)
        usd_twd = fetch_usd_twd_rate()

    tw_e    = enrich_holdings(tw_h, prices, usd_twd)
    us_e    = enrich_holdings(us_h, prices, usd_twd)
    summary = portfolio_summary(tw_e, us_e)
    if summary["total_value_twd"] > 0:
        save_snapshot(summary["total_value_twd"], summary["total_pnl_twd"],
                      summary["pnl_pct"])

    pnl_1d  = get_pnl_change(1)
    pnl_7d  = get_pnl_change(7)
    pnl_30d = get_pnl_change(30)

    # ── Header ────────────────────────────────────────────────────────────────
    h1, h2 = st.columns([5, 1])
    with h1:
        st.markdown(
            "<div style='font-size:1.4rem;font-weight:700'>📈 Investment Dashboard</div>"
            f"<div style='font-size:0.78rem;color:#8B949E'>"
            f"Updated: {datetime.now().strftime('%Y/%m/%d %H:%M')}"
            f"&nbsp;&nbsp;USD/TWD: {usd_twd:.2f} (Cathay)</div>",
            unsafe_allow_html=True)
    with h2:
        bc1, bc2 = st.columns(2)
        with bc1:
            if st.button("⟳", use_container_width=True, help="Refresh prices"):
                st.cache_data.clear(); st.rerun()
        with bc2:
            if st.button("👤", use_container_width=True, help="Account settings"):
                st.session_state._show_profile = not st.session_state.get(
                    "_show_profile", False)
                st.rerun()

    if st.session_state.get("_show_profile"):
        _profile_panel()

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

    # ── Two tabs ──────────────────────────────────────────────────────────────
    tab_dash, tab_upload = st.tabs(["📊 Dashboard", "📤 Upload"])

    with tab_dash:
        _section_charts(tw_e, us_e, summary)
        _section_holdings(tw_e, us_e)
        _section_pnl_history(tw_h, us_h)
        _section_pledge(prices, usd_twd)

    with tab_upload:
        _tab_upload(tw_h, us_h)


# ── Profile panel ─────────────────────────────────────────────────────────────
def _profile_panel():
    uname   = st.session_state.username
    profile = get_profile(uname)
    with st.container(border=True):
        st.markdown(f"#### 👤 Account Settings — {uname}")
        c1, c2 = st.columns(2)
        with c1:
            with st.form("profile_form"):
                new_email = st.text_input("Email", value=profile.get("email", ""))
                new_pw    = st.text_input("New password (leave blank to keep)",
                                          type="password")
                new_pw2   = st.text_input("Confirm new password", type="password")
                if st.form_submit_button("Save", type="primary"):
                    if new_pw and new_pw != new_pw2:
                        st.error("Passwords do not match")
                    else:
                        update_profile(uname, new_password=new_pw or None,
                                       new_email=new_email)
                        st.success("Saved!")
                        st.session_state._show_profile = False
                        st.rerun()
        with c2:
            st.caption("2FA status")
            st.write("✅ Enabled" if profile.get("totp_enabled") else "❌ Disabled")
            st.caption("Logged in as")
            st.write(uname)
            if st.button("Close", use_container_width=True):
                st.session_state._show_profile = False; st.rerun()
            if st.button("Sign Out", use_container_width=True, type="secondary"):
                logout(); st.rerun()


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
def _section_holdings(tw_e, us_e):
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
    m3.metric("US Cost Basis",  fmt(us_cb))
    m4.metric("US Market Value (USD)",
              fmt(sum(h["market_value"] or 0 for h in us_e), prefix="$"),
              fmtpnl(sum(h["unrealized_pnl_twd"] or 0 for h in us_e)),
              delta_color=dc(sum(h["unrealized_pnl_twd"] or 0 for h in us_e)))


# ═════════════════════════════════════════════════════════════════════════════
# SECTION: P&L Change History
# ═════════════════════════════════════════════════════════════════════════════
def _section_pnl_history(tw_h, us_h):
    st.markdown("<div class='section-title'>P&L Change History</div>",
                unsafe_allow_html=True)

    tw_json = json.dumps(tw_h)
    us_json = json.dumps(us_h)

    h1, h2, h3 = st.tabs(["📅 Daily (60d)", "📆 Monthly (This Year)", "📊 Annual (3Y)"])

    with h1:
        with st.spinner("Loading price history…"):
            df90 = _cached_history(tw_json, us_json, 90)
        if df90.empty:
            st.info("No data yet."); return
        daily = df90["daily_pnl_change"].dropna()
        _render(_chart_pnl_bars(daily.tail(60), "Daily P&L Change (last 60 trading days)"))
        _stats_row(daily)

    with h2:
        with st.spinner("Loading monthly data…"):
            this_year = datetime.now().year
            df_yr = _cached_history(tw_json, us_json, 365)
        if df_yr.empty:
            st.info("No data yet."); return
        monthly = (df_yr["daily_pnl_change"]
                   .dropna()[df_yr.index.year == this_year]
                   .resample("MS").sum())
        df_m = pd.DataFrame({
            "month": monthly.index.strftime("%b"),
            "pnl":   monthly.values,
        })
        _render(_chart_pnl_categorical(df_m, "month",
                                        f"Monthly P&L Change — {this_year}"))
        _stats_row(monthly)

    with h3:
        with st.spinner("Loading 3-year history (first load may take ~10 s)…"):
            df3y = _cached_history(tw_json, us_json, 1095)
        if df3y.empty:
            st.info("No data yet."); return
        annual = df3y["daily_pnl_change"].dropna().resample("YS").sum()
        df_a = pd.DataFrame({
            "year": annual.index.strftime("%Y"),
            "pnl":  annual.values,
        })
        _render(_chart_pnl_categorical(df_a, "year", "Annual P&L Change (last 3 years)"))
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


# ═════════════════════════════════════════════════════════════════════════════
# SECTION: Pledge Monitoring (read-only table in Dashboard)
# ═════════════════════════════════════════════════════════════════════════════
def _section_pledge(prices, usd_twd):
    from utils.gsheets import (is_configured as sheets_ok, load_pledge_from_sheet,
                                sheet_url as gsheet_url)

    if sheets_ok():
        loans = load_pledge_from_sheet() or load_pledge_config().get("loans", [])
    else:
        loans = load_pledge_config().get("loans", [])

    if not loans:
        return  # No pledge data → hide this section entirely

    st.markdown("<div class='section-title'>Pledge Monitoring</div>",
                unsafe_allow_html=True)

    # Fetch prices for pledged symbols
    p_syms = tuple({s["symbol"] for loan in loans for s in loan.get("pledged_stocks", [])})
    p_prices = fetch_current_prices(p_syms) if p_syms else {}

    rows = []
    total_pledge_value = 0.0
    total_loan_amount  = 0.0

    for loan in loans:
        ratio, p_value = compute_pledge_ratio(
            loan["pledged_stocks"], p_prices, loan["loan_amount_twd"], usd_twd
        )
        stocks_str = ", ".join(
            f"{_sym(s['symbol'], s.get('currency','TWD'))}×{s['shares']:,}"
            for s in loan.get("pledged_stocks", [])
        )
        total_pledge_value += p_value or 0
        total_loan_amount  += loan["loan_amount_twd"]

        if ratio is None:
            status = "⚠️ N/A"
        elif ratio < PLEDGE_CRITICAL:
            status = "🔴 MARGIN CALL"
        elif ratio < PLEDGE_WARNING:
            status = "🟠 Warning"
        elif ratio < PLEDGE_SAFE:
            status = "🟡 Watch"
        else:
            status = "🟢 Safe"

        rows.append({
            "Description":     loan["description"],
            "Pledged Stocks":  stocks_str,
            "Pledged Value":   fmt(p_value) if p_value else "—",
            "Loan Amount":     fmt(loan["loan_amount_twd"]),
            "Rate":            f"{loan.get('interest_rate', 0):.1f}%",
            "Ratio":           f"{ratio:.1f}%" if ratio is not None else "—",
            "Status":          status,
        })

    # Overall row
    if total_loan_amount > 0:
        overall_ratio = total_pledge_value / total_loan_amount * 100
        if overall_ratio < PLEDGE_CRITICAL:
            ov_status = "🔴 MARGIN CALL"
        elif overall_ratio < PLEDGE_WARNING:
            ov_status = "🟠 Warning"
        elif overall_ratio < PLEDGE_SAFE:
            ov_status = "🟡 Watch"
        else:
            ov_status = "🟢 Safe"

        rows.append({
            "Description":    "**Overall**",
            "Pledged Stocks": "",
            "Pledged Value":  fmt(total_pledge_value),
            "Loan Amount":    fmt(total_loan_amount),
            "Rate":           "",
            "Ratio":          f"{overall_ratio:.1f}%",
            "Status":         ov_status,
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Show gauge for overall ratio
    if total_loan_amount > 0:
        _render(_chart_pledge_gauge(overall_ratio), height=100)


# ═════════════════════════════════════════════════════════════════════════════
# TAB: Upload (CSV + Pledge Management)
# ═════════════════════════════════════════════════════════════════════════════
def _tab_upload(tw_h, us_h):
    # ── CSV Uploads ───────────────────────────────────────────────────────────
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

    # ── Price History Chart ───────────────────────────────────────────────────
    st.markdown("<div class='section-title'>Individual Stock Price</div>",
                unsafe_allow_html=True)

    all_h = tw_h + us_h
    if all_h:
        col_a, col_b, col_c = st.columns([3, 1, 1])
        options = [f"{_sym(h['symbol'], h['currency'])} ({h['name']})" for h in all_h]
        sel     = col_a.selectbox("Ticker", options, key="price_sel")
        period  = col_b.radio("Period", ["3M", "6M", "1Y", "2Y"],
                               horizontal=True, key="price_per")
        idx     = options.index(sel)
        h_obj   = all_h[idx]
        sym     = h_obj["symbol"]
        is_us   = h_obj["currency"] == "USD"
        days    = {"3M": 90, "6M": 180, "1Y": 365, "2Y": 730}[period]
        lc      = COLOR_PURPLE if is_us else COLOR_NEUTRAL
        pref    = "$" if is_us else "NT$"
        with st.spinner(f"Fetching {_sym(sym, h_obj['currency'])} price history…"):
            hist = fetch_historical_prices(sym, days)
        ch = _chart_price(hist, sym, h_obj.get("cost_per_share"), lc, pref)
        if ch:
            _render(ch)
            if not hist.empty:
                s = hist[sym].dropna()
                chg = s.iloc[-1] - s.iloc[0]; chg_pct = chg / s.iloc[0] * 100
                with col_c:
                    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
                    st.metric("Current",     f"{pref}{s.iloc[-1]:.2f}")
                    st.metric("Period Chg",  f"{chg_pct:+.2f}%", delta_color=dc(chg))
                    if h_obj.get("cost_per_share"):
                        diff = s.iloc[-1] - h_obj["cost_per_share"]
                        st.metric("vs Cost",  f"{diff:+.2f}", delta_color=dc(diff))
        else:
            st.warning(f"No price data for {sym}")

    # ── Pledge Management ─────────────────────────────────────────────────────
    st.markdown("<div class='section-title'>Pledge Management</div>",
                unsafe_allow_html=True)

    from utils.gsheets import (is_configured as sheets_ok, load_pledge_from_sheet,
                                save_pledge_to_sheet, sheet_url as gsheet_url)

    if sheets_ok():
        url = gsheet_url()
        st.success(f"✅ Pledge data synced to Google Sheets — [Open Sheet]({url})")
        loans = load_pledge_from_sheet() or load_pledge_config().get("loans", [])
    else:
        loans = load_pledge_config().get("loans", [])
        with st.expander("ℹ️ Google Sheets not configured"):
            st.markdown("""
1. Enable **Google Sheets API** in Google Cloud Console (same project as Drive API)
2. Create a Google Sheet, rename the first tab to `質押明細`
3. Share with the service account email (Editor)
4. Copy the Sheet ID from the URL: `.../spreadsheets/d/<ID>/edit`
5. Add `GOOGLE_PLEDGE_SHEET_ID` env var in Railway

Sheet columns: `說明 | 借款金額TWD | 年利率% | 借款日期 | 質押代號 | 質押股數 | 幣別`
""")

    ALL_SYMS = list(TW_TICKERS.keys()) + list(US_TICKERS.keys())

    with st.expander("➕ Add Pledge"):
        with st.form("add_pledge"):
            c1, c2 = st.columns(2)
            with c1:
                desc      = st.text_input("Description", placeholder="Pledge A")
                loan_amt  = st.number_input("Loan Amount (TWD)", min_value=0,
                                             step=10000, value=500000)
                interest  = st.number_input("Annual Rate (%)", 0.0, 20.0, 2.5, 0.1)
                loan_date = st.date_input("Date", value=date.today())
            with c2:
                p_syms   = st.multiselect("Pledged Stocks", ALL_SYMS)
                p_shares = {s: st.number_input(f"{_sym(s, 'TWD' if s in TW_TICKERS else 'USD')}"
                                                f" shares", 0, step=100, value=1000,
                                                key=f"ps_{s}")
                            for s in p_syms}
            if st.form_submit_button("Add", use_container_width=True, type="primary"):
                if loan_amt > 0 and p_syms:
                    new_id = (max(l["id"] for l in loans) + 1) if loans else 1
                    loans.append({
                        "id": new_id,
                        "description": desc or f"Pledge {new_id}",
                        "pledged_stocks": [
                            {"symbol": s, "shares": p_shares[s],
                             "currency": "TWD" if s in TW_TICKERS else "USD"}
                            for s in p_syms],
                        "loan_amount_twd": loan_amt,
                        "interest_rate":   interest,
                        "date":            str(loan_date),
                    })
                    save_pledge_config({"loans": loans})
                    if sheets_ok():
                        save_pledge_to_sheet(loans)
                    st.success("Added"); st.rerun()

    if loans:
        with st.expander("🗑 Delete Pledge"):
            del_c = st.selectbox("Select to delete",
                                  ["—"] + [f"#{l['id']} {l['description']}"
                                           for l in loans])
            if del_c != "—" and st.button("Delete selected"):
                del_id    = int(del_c.split()[0].replace("#", ""))
                new_loans = [l for l in loans if l["id"] != del_id]
                save_pledge_config({"loans": new_loans})
                if sheets_ok():
                    save_pledge_to_sheet(new_loans)
                st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════
if not st.session_state.get("authenticated"):
    show_auth()
else:
    render_dashboard()
