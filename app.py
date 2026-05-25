import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from datetime import datetime

from config.settings import (
    APP_NAME, APP_ICON,
    COLOR_POSITIVE, COLOR_NEGATIVE, COLOR_NEUTRAL, COLOR_WARNING, COLOR_PURPLE,
    PLOTLY_LAYOUT, PLEDGE_CRITICAL, PLEDGE_WARNING, PLEDGE_SAFE,
    TW_TICKERS, US_TICKERS, TW_CSV_FILE, US_CSV_FILE,
)
from utils.auth import (
    has_users, create_user, verify_password, verify_totp,
    is_totp_enabled, get_totp_qr_bytes, logout,
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
from utils.history_manager import (
    save_snapshot, get_pnl_change, history_to_dataframe,
)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title=APP_NAME,
    page_icon=APP_ICON,
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
#MainMenu, footer, header { visibility: hidden; }
.stDeployButton { display: none; }

/* Tab bar */
.stTabs [data-baseweb="tab-list"] {
    background: #161B22;
    border-radius: 10px;
    padding: 4px 6px;
    gap: 2px;
    border-bottom: none !important;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 7px;
    color: #8B949E;
    font-weight: 500;
    font-size: 0.9rem;
    padding: 7px 18px;
    border: none !important;
}
.stTabs [aria-selected="true"] {
    background: #0D1117 !important;
    color: #E6EDF3 !important;
}
.stTabs [data-baseweb="tab-highlight"] { display: none; }
.stTabs [data-baseweb="tab-border"]    { display: none; }

/* Metric cards */
div[data-testid="stMetric"] {
    background: #161B22;
    border: 1px solid #30363D;
    border-radius: 10px;
    padding: 14px 18px !important;
}
div[data-testid="stMetricLabel"] > div {
    font-size: 0.78rem !important;
    color: #8B949E !important;
}
div[data-testid="stMetricValue"] > div {
    font-size: 1.4rem !important;
}

/* Dashboard title bar */
.dash-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 0 16px 0;
    border-bottom: 1px solid #30363D;
    margin-bottom: 16px;
}
.dash-title { font-size: 1.4rem; font-weight: 700; color: #E6EDF3; }
.dash-meta  { font-size: 0.8rem; color: #8B949E; }

/* Section headers */
.section-title {
    font-size: 1.0rem;
    font-weight: 600;
    color: #E6EDF3;
    margin: 16px 0 8px 0;
    padding-left: 4px;
    border-left: 3px solid #00C896;
}

/* Status badges */
.badge-safe     { color: #00C896; font-weight: 600; }
.badge-watch    { color: #4A90D9; font-weight: 600; }
.badge-warn     { color: #FFB74D; font-weight: 600; }
.badge-critical { color: #FF4B5C; font-weight: 600; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt(val: float, prefix="NT$") -> str:
    if abs(val) >= 1_000_000:
        return f"{prefix}{val/1_000_000:.2f}M"
    if abs(val) >= 1_000:
        return f"{prefix}{val/1_000:.1f}K"
    return f"{prefix}{val:,.0f}"


def dc(val: float) -> str:
    return "normal" if val >= 0 else "inverse"


def plotly_chart(fig, height=None):
    if height:
        fig.update_layout(height=height)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ═════════════════════════════════════════════════════════════════════════════
# AUTH SCREENS
# ═════════════════════════════════════════════════════════════════════════════
def show_setup():
    st.markdown("<div style='max-width:480px;margin:60px auto'>", unsafe_allow_html=True)
    st.markdown(f"## {APP_ICON} 初始化設定")
    st.info("歡迎！請建立你的管理員帳號。")
    with st.form("setup"):
        uname = st.text_input("使用者名稱")
        pw1   = st.text_input("密碼（至少 8 字元）", type="password")
        pw2   = st.text_input("確認密碼", type="password")
        totp  = st.checkbox("啟用 Google Authenticator 雙因子驗證", value=True)
        ok    = st.form_submit_button("建立帳號", use_container_width=True, type="primary")
    if ok:
        if not uname or not pw1:
            st.error("帳號和密碼不能為空"); return
        if len(pw1) < 8:
            st.error("密碼至少 8 個字元"); return
        if pw1 != pw2:
            st.error("兩次密碼不一致"); return
        success, secret = create_user(uname, pw1, totp)
        if not success:
            st.error("帳號已存在"); return
        st.success(f"帳號 **{uname}** 建立成功！")
        if totp and secret:
            st.markdown("### 掃描 QR Code 綁定 Google Authenticator")
            c1, c2 = st.columns([1, 2])
            with c1:
                st.image(get_totp_qr_bytes(uname, secret), width=200)
            with c2:
                st.code(secret)
                st.caption("手動輸入金鑰，或用 Authy / Google Authenticator 掃描")
            st.warning("請先完成綁定再登入。")
    st.markdown("</div>", unsafe_allow_html=True)


def show_login():
    st.markdown("<div style='max-width:420px;margin:80px auto'>", unsafe_allow_html=True)
    st.markdown(f"<div style='text-align:center;font-size:2rem;margin-bottom:8px'>{APP_ICON}</div>", unsafe_allow_html=True)
    st.markdown(f"<div style='text-align:center;font-size:1.5rem;font-weight:700;margin-bottom:24px'>{APP_NAME}</div>", unsafe_allow_html=True)
    with st.form("login"):
        uname = st.text_input("帳號", placeholder="Username")
        pw    = st.text_input("密碼", type="password", placeholder="Password")
        code  = st.text_input("驗證碼 (2FA)", placeholder="6 位數字", max_chars=6)
        ok    = st.form_submit_button("登入", use_container_width=True, type="primary")
    if ok:
        if not verify_password(uname, pw):
            st.error("帳號或密碼錯誤"); return
        if is_totp_enabled(uname):
            if not code:
                st.warning("請輸入 Google Authenticator 驗證碼"); return
            if not verify_totp(uname, code):
                st.error("驗證碼錯誤或已過期"); return
        st.session_state.authenticated = True
        st.session_state.username = uname
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═════════════════════════════════════════════════════════════════════════════
def render_dashboard():
    # ── Google Drive sync (once per session) ─────────────────────────────────
    if not st.session_state.get("_gdrive_synced"):
        try:
            from utils.gdrive import sync_down_all
            sync_down_all()
        except Exception:
            pass
        st.session_state._gdrive_synced = True

    # ── Load all market data ──────────────────────────────────────────────────
    tw_holdings = load_tw_holdings()
    us_holdings = load_us_holdings()
    all_syms    = tuple(h["symbol"] for h in tw_holdings + us_holdings)

    with st.spinner(""):
        prices  = fetch_current_prices(all_syms)
        usd_twd = fetch_usd_twd_rate()

    tw_enriched = enrich_holdings(tw_holdings, prices, usd_twd)
    us_enriched = enrich_holdings(us_holdings, prices, usd_twd)
    summary     = portfolio_summary(tw_enriched, us_enriched)

    # Save snapshot silently
    if summary["total_value_twd"] > 0:
        save_snapshot(summary["total_value_twd"], summary["total_pnl_twd"], summary["pnl_pct"])

    pnl_1d  = get_pnl_change(1)
    pnl_7d  = get_pnl_change(7)
    pnl_30d = get_pnl_change(30)
    now_str = datetime.now().strftime("%Y/%m/%d %H:%M")

    # ── Header bar ────────────────────────────────────────────────────────────
    h1, h2 = st.columns([4, 1])
    with h1:
        st.markdown(
            f"<div class='dash-title'>{APP_ICON} {APP_NAME}</div>"
            f"<div class='dash-meta'>更新：{now_str}　USD/TWD：{usd_twd:.2f}</div>",
            unsafe_allow_html=True,
        )
    with h2:
        st.markdown("<div style='text-align:right'>", unsafe_allow_html=True)
        if st.button("⟳ 更新", use_container_width=True):
            st.cache_data.clear(); st.rerun()
        st.caption(f"👤 {st.session_state.username}")
        if st.button("登出", use_container_width=True):
            logout(); st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div style='border-bottom:1px solid #30363D;margin-bottom:12px'></div>", unsafe_allow_html=True)

    # ── KPI Row ───────────────────────────────────────────────────────────────
    pnl = summary["total_pnl_twd"]
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("總市值",   fmt(summary["total_value_twd"]))
    k2.metric("未實現損益", fmt(pnl),
              f"{summary['pnl_pct']:+.2f}%", delta_color=dc(pnl))
    k3.metric("今日損益",
              fmt(pnl_1d) if pnl_1d is not None else "—",
              delta_color=dc(pnl_1d or 0))
    k4.metric("近 7 日損益",
              fmt(pnl_7d) if pnl_7d is not None else "—",
              delta_color=dc(pnl_7d or 0))
    k5.metric("近 30 日損益",
              fmt(pnl_30d) if pnl_30d is not None else "—",
              delta_color=dc(pnl_30d or 0))

    st.markdown("<div style='margin-top:12px'></div>", unsafe_allow_html=True)

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📊 總覽", "🇹🇼 台股", "🇺🇸 美股", "📈 損益歷史", "🏦 質押監控"
    ])

    with tab1:
        _tab_overview(tw_enriched, us_enriched, summary)

    with tab2:
        _tab_tw(tw_holdings, tw_enriched, prices)

    with tab3:
        _tab_us(us_holdings, us_enriched, prices, usd_twd)

    with tab4:
        _tab_history(tw_holdings, us_holdings)

    with tab5:
        _tab_pledge(prices, usd_twd)


# ═════════════════════════════════════════════════════════════════════════════
# TAB 1 — 總覽
# ═════════════════════════════════════════════════════════════════════════════
def _tab_overview(tw_enriched, us_enriched, summary):
    all_enriched = tw_enriched + us_enriched

    c1, c2, c3 = st.columns([5, 4, 5])

    # Allocation donut
    with c1:
        st.markdown("<div class='section-title'>持股配置</div>", unsafe_allow_html=True)
        alloc = [
            {"標的": h["name"], "市值": h["market_value_twd"] or 0}
            for h in all_enriched if (h["market_value_twd"] or 0) > 0
        ]
        if alloc:
            fig = px.pie(
                pd.DataFrame(alloc), names="標的", values="市值", hole=0.45,
                color_discrete_sequence=["#00C896","#4A90D9","#A855F7","#FFB74D","#FF4B5C"],
            )
            fig.update_layout(**PLOTLY_LAYOUT, showlegend=True,
                               legend=dict(orientation="v", x=1.0, font_size=11))
            fig.update_traces(textinfo="percent", textfont_size=11)
            plotly_chart(fig, height=260)

    # TW/US split
    with c2:
        st.markdown("<div class='section-title'>台股 / 美股</div>", unsafe_allow_html=True)
        tw_v, us_v = summary["tw_value_twd"], summary["us_value_twd"]
        total_v = tw_v + us_v
        if total_v > 0:
            fig2 = go.Figure(go.Pie(
                labels=["🇹🇼 台股", "🇺🇸 美股"],
                values=[tw_v, us_v], hole=0.55,
                marker_colors=[COLOR_NEUTRAL, COLOR_PURPLE],
                textinfo="label+percent", textfont_size=12,
            ))
            fig2.update_layout(
                **PLOTLY_LAYOUT, showlegend=False,
                annotations=[dict(
                    text=f"{fmt(total_v)}", x=0.5, y=0.5,
                    font_size=12, showarrow=False, font_color="#E6EDF3",
                )],
            )
            plotly_chart(fig2, height=260)

    # P&L bar by stock
    with c3:
        st.markdown("<div class='section-title'>各標的損益</div>", unsafe_allow_html=True)
        rows = [
            {"標的": h["name"], "損益": h["unrealized_pnl_twd"] or 0}
            for h in all_enriched
        ]
        df = pd.DataFrame(rows).sort_values("損益")
        colors = [COLOR_POSITIVE if v >= 0 else COLOR_NEGATIVE for v in df["損益"]]
        fig3 = go.Figure(go.Bar(
            x=df["標的"], y=df["損益"],
            marker_color=colors,
            text=[fmt(v) for v in df["損益"]], textposition="outside", textfont_size=10,
        ))
        fig3.update_layout(**PLOTLY_LAYOUT, yaxis_title="TWD")
        plotly_chart(fig3, height=260)

    # Holdings table
    st.markdown("<div class='section-title'>完整持倉</div>", unsafe_allow_html=True)
    rows = []
    for h in all_enriched:
        rows.append({
            "市場":    "🇹🇼 台股" if h["currency"] == "TWD" else "🇺🇸 美股",
            "代號":    h["symbol"],
            "名稱":    h["name"],
            "股數":    h["shares"],
            "成本均價": f"{h['cost_per_share']:.2f}",
            "現價":    f"{h['current_price']:.2f}" if h["current_price"] else "—",
            "成本":    f"{h['cost_basis']:,.0f}",
            "市值":    f"{h['market_value']:,.0f}" if h["market_value"] else "—",
            "損益":    f"{h['unrealized_pnl']:+,.0f}" if h["unrealized_pnl"] is not None else "—",
            "損益率":  f"{h['pnl_pct']:+.2f}%" if h["pnl_pct"] is not None else "—",
            "幣別":    h["currency"],
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Snapshot P&L trend
    df_snap = history_to_dataframe()
    if not df_snap.empty and len(df_snap) > 1:
        st.markdown("<div class='section-title'>損益歷史快照</div>", unsafe_allow_html=True)
        last_pnl = df_snap["total_pnl_twd"].iloc[-1]
        lc = COLOR_POSITIVE if last_pnl >= 0 else COLOR_NEGATIVE
        fc = "rgba(0,200,150,0.1)" if last_pnl >= 0 else "rgba(255,75,92,0.1)"
        fig4 = go.Figure(go.Scatter(
            x=df_snap.index, y=df_snap["total_pnl_twd"],
            mode="lines", line=dict(color=lc, width=2),
            fill="tozeroy", fillcolor=fc,
        ))
        fig4.add_hline(y=0, line_color="rgba(255,255,255,0.25)", line_dash="dash")
        fig4.update_layout(**PLOTLY_LAYOUT, yaxis_title="NT$")
        plotly_chart(fig4, height=200)


# ═════════════════════════════════════════════════════════════════════════════
# TAB 2 — 台股
# ═════════════════════════════════════════════════════════════════════════════
def _tab_tw(tw_holdings, tw_enriched, prices):
    # Upload section
    with st.expander("📂 上傳國泰證券台股 CSV"):
        st.caption("欄位：股票代號、股票名稱、庫存股數、平均成本、成本金額")
        up = st.file_uploader("選擇 CSV", type=["csv"], key="up_tw")
        if up:
            TW_CSV_FILE.write_bytes(up.getvalue())
            try:
                from utils.gdrive import upload
                upload(TW_CSV_FILE)
            except Exception:
                pass
            st.success("上傳成功！"); st.cache_data.clear(); st.rerun()

    # Metrics
    total_cost  = sum(h["cost_basis"]         for h in tw_enriched)
    total_value = sum(h["market_value"] or 0  for h in tw_enriched)
    total_pnl   = sum(h["unrealized_pnl"] or 0 for h in tw_enriched)
    pnl_pct     = total_pnl / total_cost * 100 if total_cost > 0 else 0

    m1, m2, m3 = st.columns(3)
    m1.metric("台股成本",  f"NT${total_cost:,.0f}")
    m2.metric("台股市值",  f"NT${total_value:,.0f}",
              f"NT${total_pnl:+,.0f} ({pnl_pct:+.2f}%)", delta_color=dc(total_pnl))
    m3.metric("持股數",    f"{len(tw_enriched)} 檔")

    # Table
    st.markdown("<div class='section-title'>持倉明細</div>", unsafe_allow_html=True)
    rows = [{
        "代號": h["symbol"], "名稱": h["name"],
        "庫存": h["shares"],
        "成本均價": f"NT${h['cost_per_share']:.2f}",
        "現價":     f"NT${h['current_price']:.2f}" if h["current_price"] else "—",
        "成本金額": f"NT${h['cost_basis']:,.0f}",
        "現值":     f"NT${h['market_value']:,.0f}" if h["market_value"] else "—",
        "損益":     f"NT${h['unrealized_pnl']:+,.0f}" if h["unrealized_pnl"] is not None else "—",
        "損益率":   f"{h['pnl_pct']:+.2f}%" if h["pnl_pct"] is not None else "—",
    } for h in tw_enriched]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Price history
    st.markdown("<div class='section-title'>個股走勢</div>", unsafe_allow_html=True)
    ca, cb = st.columns([2, 1])
    with ca:
        sel = st.selectbox("股票", [f"{h['symbol']} {h['name']}" for h in tw_enriched], key="tw_sel")
    with cb:
        period = st.radio("期間", ["3M", "6M", "1Y"], horizontal=True, key="tw_period")
    sym  = sel.split()[0]
    days = {"3M": 90, "6M": 180, "1Y": 365}[period]
    hist = fetch_historical_prices(sym, days)
    h_obj = next((h for h in tw_enriched if h["symbol"] == sym), None)
    _price_chart(hist, sym, h_obj, prefix="NT$")


# ═════════════════════════════════════════════════════════════════════════════
# TAB 3 — 美股
# ═════════════════════════════════════════════════════════════════════════════
def _tab_us(us_holdings, us_enriched, prices, usd_twd):
    with st.expander("📂 上傳國泰證券美股 CSV"):
        st.caption("欄位：Symbol、Name、Shares、Avg Cost (USD)、Total Cost (USD)")
        up = st.file_uploader("選擇 CSV", type=["csv"], key="up_us")
        if up:
            US_CSV_FILE.write_bytes(up.getvalue())
            try:
                from utils.gdrive import upload
                upload(US_CSV_FILE)
            except Exception:
                pass
            st.success("上傳成功！"); st.cache_data.clear(); st.rerun()

    total_cost_usd  = sum(h["cost_basis"]          for h in us_enriched)
    total_value_usd = sum(h["market_value"] or 0   for h in us_enriched)
    total_pnl_usd   = sum(h["unrealized_pnl"] or 0 for h in us_enriched)
    pnl_pct         = total_pnl_usd / total_cost_usd * 100 if total_cost_usd > 0 else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("美股成本",   f"${total_cost_usd:,.0f}")
    m2.metric("美股市值",   f"${total_value_usd:,.0f}",
              f"${total_pnl_usd:+,.0f} ({pnl_pct:+.2f}%)", delta_color=dc(total_pnl_usd))
    m3.metric("折合台幣",   f"NT${total_value_usd * usd_twd:,.0f}")
    m4.metric("匯率",       f"1 USD = {usd_twd:.2f}")

    st.markdown("<div class='section-title'>持倉明細</div>", unsafe_allow_html=True)
    rows = [{
        "代號": h["symbol"], "名稱": h["name"],
        "股數": h["shares"],
        "成本均價": f"${h['cost_per_share']:.2f}",
        "現價":     f"${h['current_price']:.2f}" if h["current_price"] else "—",
        "成本(USD)": f"${h['cost_basis']:,.2f}",
        "市值(USD)": f"${h['market_value']:,.2f}" if h["market_value"] else "—",
        "市值(TWD)": f"NT${h['market_value_twd']:,.0f}" if h["market_value_twd"] else "—",
        "損益(USD)": f"${h['unrealized_pnl']:+,.2f}" if h["unrealized_pnl"] is not None else "—",
        "損益率":    f"{h['pnl_pct']:+.2f}%" if h["pnl_pct"] is not None else "—",
    } for h in us_enriched]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.markdown("<div class='section-title'>個股走勢</div>", unsafe_allow_html=True)
    ca, cb = st.columns([2, 1])
    with ca:
        sel = st.selectbox("股票", [f"{h['symbol']} {h['name']}" for h in us_enriched], key="us_sel")
    with cb:
        period = st.radio("期間", ["3M", "6M", "1Y", "2Y"], horizontal=True, key="us_period")
    sym  = sel.split()[0]
    days = {"3M": 90, "6M": 180, "1Y": 365, "2Y": 730}[period]
    hist = fetch_historical_prices(sym, days)
    h_obj = next((h for h in us_enriched if h["symbol"] == sym), None)
    _price_chart(hist, sym, h_obj, prefix="$", line_color=COLOR_PURPLE)


def _price_chart(hist: pd.DataFrame, sym: str, holding, prefix="NT$", line_color=COLOR_NEUTRAL):
    if hist.empty:
        st.warning(f"無法取得 {sym} 歷史價格"); return
    s = hist[sym].dropna()
    start_p, end_p = s.iloc[0], s.iloc[-1]
    chg, chg_pct   = end_p - start_p, (end_p - start_p) / start_p * 100
    cost_p = holding["cost_per_share"] if holding else None

    fig = go.Figure(go.Scatter(
        x=s.index, y=s.values, mode="lines",
        line=dict(color=line_color, width=2),
        fill="tozeroy",
        fillcolor=f"rgba({int(line_color[1:3],16)},{int(line_color[3:5],16)},{int(line_color[5:7],16)},0.08)",
    ))
    if cost_p:
        fig.add_hline(y=cost_p, line_dash="dash", line_color="#FFB74D",
                      annotation_text=f"成本 {prefix}{cost_p:.2f}",
                      annotation_position="bottom right")
    fig.update_layout(**PLOTLY_LAYOUT, yaxis_title=f"{prefix}", height=300)
    plotly_chart(fig)

    sc1, sc2, sc3 = st.columns(3)
    sc1.metric("期初", f"{prefix}{start_p:.2f}")
    sc2.metric("現價", f"{prefix}{end_p:.2f}")
    sc3.metric("期間漲跌", f"{prefix}{chg:+.2f} ({chg_pct:+.2f}%)", delta_color=dc(chg))


# ═════════════════════════════════════════════════════════════════════════════
# TAB 4 — 損益歷史
# ═════════════════════════════════════════════════════════════════════════════
def _tab_history(tw_holdings, us_holdings):
    ca, cb = st.columns([3, 1])
    with ca:
        st.markdown("<div class='section-title'>損益歷史分析</div>", unsafe_allow_html=True)
    with cb:
        days = st.selectbox("回溯期間", [90, 180, 365],
                            format_func=lambda x: f"{x} 天", key="hist_days")

    with st.spinner("計算歷史損益..."):
        tw_ph = {h["symbol"]: fetch_historical_prices(h["symbol"], days) for h in tw_holdings}
        us_ph = {h["symbol"]: fetch_historical_prices(h["symbol"], days) for h in us_holdings}
        usd_h = fetch_usd_twd_history(days)
        df    = compute_portfolio_history(tw_holdings, us_holdings, tw_ph, us_ph, usd_h, days)

    t1, t2, t3 = st.tabs(["📈 趨勢", "📊 每日/週", "📅 月度"])

    with t1:
        if df.empty:
            st.info("歷史資料不足，請確認 API 連線。"); return
        last_pnl = df["total_pnl_twd"].iloc[-1]
        lc = COLOR_POSITIVE if last_pnl >= 0 else COLOR_NEGATIVE
        fc = "rgba(0,200,150,0.1)" if last_pnl >= 0 else "rgba(255,75,92,0.1)"

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df.index, y=df["total_pnl_twd"], name="未實現損益", yaxis="y",
            line=dict(color=lc, width=2.5), fill="tozeroy", fillcolor=fc,
        ))
        fig.add_trace(go.Scatter(
            x=df.index, y=df["total_value_twd"], name="總市值", yaxis="y2",
            line=dict(color=COLOR_NEUTRAL, width=1.5, dash="dot"),
        ))
        fig.add_hline(y=0, line_color="rgba(255,255,255,0.2)", line_dash="dash")
        layout = dict(**PLOTLY_LAYOUT)
        layout.update(yaxis=dict(title="未實現損益 NT$", gridcolor="#30363D"),
                      yaxis2=dict(title="總市值 NT$", overlaying="y", side="right",
                                  gridcolor="rgba(0,0,0,0)"),
                      legend=dict(orientation="h", y=1.05), height=360)
        fig.update_layout(**layout)
        plotly_chart(fig)

        pnl_s = df["total_pnl_twd"].dropna()
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("期間最高", fmt(pnl_s.max()))
        s2.metric("期間最低", fmt(pnl_s.min()))
        s3.metric("期間變化", fmt(pnl_s.iloc[-1] - pnl_s.iloc[0]),
                  delta_color=dc(pnl_s.iloc[-1] - pnl_s.iloc[0]))
        s4.metric("損益率",   f"{df['pnl_pct'].iloc[-1]:+.2f}%")

    with t2:
        if df.empty: return
        daily = df["daily_pnl_change"].dropna().tail(60)
        colors_d = [COLOR_POSITIVE if v >= 0 else COLOR_NEGATIVE for v in daily]
        fig2 = go.Figure(go.Bar(
            x=daily.index, y=daily.values, marker_color=colors_d,
            text=[f"${v/1000:+.1f}K" for v in daily.values],
            textposition="outside", textfont_size=9,
        ))
        fig2.add_hline(y=0, line_color="rgba(255,255,255,0.2)")
        fig2.update_layout(**PLOTLY_LAYOUT, yaxis_title="每日損益 NT$", height=300,
                            title="近 60 日每日損益")
        plotly_chart(fig2)

        weekly = df["daily_pnl_change"].dropna().resample("W").sum()
        colors_w = [COLOR_POSITIVE if v >= 0 else COLOR_NEGATIVE for v in weekly]
        fig3 = go.Figure(go.Bar(
            x=weekly.index.strftime("%m/%d"), y=weekly.values,
            marker_color=colors_w,
        ))
        fig3.update_layout(**PLOTLY_LAYOUT, yaxis_title="週損益 NT$", height=250, title="每週損益")
        plotly_chart(fig3)

    with t3:
        if df.empty: return
        monthly = df["daily_pnl_change"].dropna().resample("ME").sum()
        df_m = pd.DataFrame({"月份": monthly.index.strftime("%Y-%m"), "損益": monthly.values})
        colors_m = [COLOR_POSITIVE if v >= 0 else COLOR_NEGATIVE for v in df_m["損益"]]

        ca2, cb2 = st.columns([3, 2])
        with ca2:
            fig4 = go.Figure(go.Bar(
                x=df_m["月份"], y=df_m["損益"], marker_color=colors_m,
                text=[fmt(v) for v in df_m["損益"]], textposition="outside", textfont_size=10,
            ))
            fig4.update_layout(**PLOTLY_LAYOUT, height=300, title="月度損益")
            plotly_chart(fig4)
        with cb2:
            pos = (df_m["損益"] > 0).sum()
            neg = (df_m["損益"] < 0).sum()
            best  = df_m.loc[df_m["損益"].idxmax()]
            worst = df_m.loc[df_m["損益"].idxmin()]
            st.markdown("<div class='section-title'>月度統計</div>", unsafe_allow_html=True)
            st.metric("獲利月",   f"{pos} 個月")
            st.metric("虧損月",   f"{neg} 個月")
            st.metric("月勝率",   f"{pos/max(pos+neg,1)*100:.1f}%")
            st.metric("最佳月份", best["月份"],   fmt(best["損益"]))
            st.metric("最差月份", worst["月份"],  fmt(worst["損益"]))


# ═════════════════════════════════════════════════════════════════════════════
# TAB 5 — 質押監控
# ═════════════════════════════════════════════════════════════════════════════
def _tab_pledge(prices, usd_twd):
    pledge_cfg = load_pledge_config()
    loans      = pledge_cfg.get("loans", [])
    ALL_SYMS   = list(TW_TICKERS.keys()) + list(US_TICKERS.keys())

    # ── Add/Delete pledge ────────────────────────────────────────────────────
    with st.expander("➕ 新增 / 管理質押設定", expanded=not loans):
        with st.form("add_pledge"):
            c1, c2 = st.columns(2)
            with c1:
                desc    = st.text_input("說明", placeholder="台股質押 A")
                loan_amt = st.number_input("借款金額（TWD）", min_value=0, step=10000, value=500000)
                interest = st.number_input("年利率（%）", 0.0, 20.0, 2.5, 0.1)
                from datetime import date
                loan_date = st.date_input("借款日期", value=date.today())
            with c2:
                pledged_syms = st.multiselect("質押股票", ALL_SYMS)
                pledged_shares = {}
                for sym in pledged_syms:
                    pledged_shares[sym] = st.number_input(
                        f"{sym} 質押股數", 0, step=100, value=1000, key=f"ps_{sym}"
                    )
            if st.form_submit_button("新增", use_container_width=True, type="primary"):
                if loan_amt > 0 and pledged_syms:
                    loans.append({
                        "id": (max(l["id"] for l in loans) + 1) if loans else 1,
                        "description": desc or f"質押 {len(loans)+1}",
                        "pledged_stocks": [
                            {"symbol": s, "shares": pledged_shares[s],
                             "currency": "TWD" if s in TW_TICKERS else "USD"}
                            for s in pledged_syms
                        ],
                        "loan_amount_twd": loan_amt,
                        "interest_rate": interest,
                        "date": str(loan_date),
                    })
                    save_pledge_config({"loans": loans})
                    st.success("已新增"); st.rerun()

        if loans:
            del_choice = st.selectbox("刪除", ["—"] + [f"#{l['id']} {l['description']}" for l in loans])
            if del_choice != "—" and st.button("刪除選取"):
                del_id = int(del_choice.split()[0].replace("#", ""))
                save_pledge_config({"loans": [l for l in loans if l["id"] != del_id]})
                st.rerun()

    if not loans:
        st.info("尚無質押設定。")
        return

    # ── Fetch pledged stock prices ────────────────────────────────────────────
    pledged_syms = tuple({s["symbol"] for loan in loans for s in loan["pledged_stocks"]})
    if pledged_syms:
        pledge_prices = fetch_current_prices(pledged_syms)
    else:
        pledge_prices = {}

    # ── Gauge cards ──────────────────────────────────────────────────────────
    for loan in loans:
        ratio, p_value = compute_pledge_ratio(
            loan["pledged_stocks"], pledge_prices, loan["loan_amount_twd"], usd_twd
        )

        if ratio is None:
            st.warning(f"**{loan['description']}** — 無法取得現價"); continue

        # Determine status
        if ratio >= PLEDGE_SAFE:
            bar_c, badge = COLOR_POSITIVE, ("badge-safe", "🟢 安全")
        elif ratio >= PLEDGE_WARNING:
            bar_c, badge = COLOR_NEUTRAL,  ("badge-watch", "🔵 觀察")
        elif ratio >= PLEDGE_CRITICAL:
            bar_c, badge = COLOR_WARNING,  ("badge-warn", "🟡 警告")
        else:
            bar_c, badge = COLOR_NEGATIVE, ("badge-critical", "🔴 追繳！")

        st.markdown(f"### {loan['description']}")

        ga, gb = st.columns([2, 1])
        with ga:
            fig_g = go.Figure(go.Indicator(
                mode="gauge+number",
                value=ratio,
                number={"suffix": "%", "font": {"size": 40, "color": "#E6EDF3"}},
                title={"text": f"維持率 　<span class='{badge[0]}'>{badge[1]}</span>",
                       "font": {"size": 15, "color": "#E6EDF3"}},
                gauge={
                    "axis": {"range": [0, 500], "tickcolor": "#8B949E",
                              "tickfont": {"color": "#8B949E"}, "tickwidth": 1},
                    "bar":  {"color": bar_c, "thickness": 0.22},
                    "bgcolor": "rgba(0,0,0,0)",
                    "bordercolor": "#30363D",
                    "steps": [
                        {"range": [0,           PLEDGE_CRITICAL], "color": "rgba(255,75,92,0.2)"},
                        {"range": [PLEDGE_CRITICAL, PLEDGE_WARNING], "color": "rgba(255,183,77,0.15)"},
                        {"range": [PLEDGE_WARNING,  PLEDGE_SAFE],    "color": "rgba(74,144,217,0.15)"},
                        {"range": [PLEDGE_SAFE,      500],           "color": "rgba(0,200,150,0.1)"},
                    ],
                    "threshold": {
                        "line": {"color": COLOR_NEGATIVE, "width": 4},
                        "thickness": 0.8, "value": PLEDGE_CRITICAL,
                    },
                },
            ))
            fig_g.update_layout(paper_bgcolor="rgba(0,0,0,0)",
                                 font={"color": "#E6EDF3"}, height=260,
                                 margin=dict(t=30, b=10, l=20, r=20))
            st.plotly_chart(fig_g, use_container_width=True, config={"displayModeBar": False})

        with gb:
            st.metric("維持率",     f"{ratio:.1f}%")
            st.metric("質押股票市值", fmt(p_value))
            st.metric("借款金額",    fmt(loan["loan_amount_twd"]))
            st.metric("年利率",      f"{loan['interest_rate']:.1f}%")
            # Estimated margin call price for TW stocks
            tw_shares = sum(s["shares"] for s in loan["pledged_stocks"] if s.get("currency") == "TWD")
            if tw_shares > 0:
                mc_val = loan["loan_amount_twd"] * PLEDGE_CRITICAL / 100
                st.metric("台股追繳均價", f"NT${mc_val/tw_shares:.2f}",
                           help="台股跌至此均價將觸發追繳（不含美股）")

        if ratio < PLEDGE_CRITICAL:
            st.error(f"⚠️ 緊急！維持率 {ratio:.1f}% 已低於追繳線 {PLEDGE_CRITICAL}%！")
        elif ratio < PLEDGE_WARNING:
            st.warning(f"⚠️ 維持率 {ratio:.1f}% 低於警戒線 {PLEDGE_WARNING}%，請注意。")

        st.markdown("---")

    # ── Historical ratio chart ────────────────────────────────────────────────
    if loans:
        st.markdown("<div class='section-title'>維持率趨勢（以首筆質押估算）</div>", unsafe_allow_html=True)
        first = loans[0]
        usd_h = fetch_usd_twd_history(90)
        ratio_s = None
        for ps in first["pledged_stocks"]:
            df_p = fetch_historical_prices(ps["symbol"], 90)
            if df_p.empty:
                continue
            fx = usd_h.reindex(df_p.index, method="ffill").fillna(usd_twd) if ps.get("currency") == "USD" else 1.0
            val = df_p[ps["symbol"]] * ps["shares"] * fx
            ratio_s = val if ratio_s is None else ratio_s + val
        if ratio_s is not None:
            ratio_pct = ratio_s / first["loan_amount_twd"] * 100
            fig_r = go.Figure()
            fig_r.add_trace(go.Scatter(
                x=ratio_pct.index, y=ratio_pct.values,
                mode="lines", line=dict(color=COLOR_NEUTRAL, width=2), name="維持率 %",
            ))
            for thresh, color, label in [
                (PLEDGE_CRITICAL, COLOR_NEGATIVE, f"追繳 {PLEDGE_CRITICAL}%"),
                (PLEDGE_WARNING,  COLOR_WARNING,  f"警戒 {PLEDGE_WARNING}%"),
                (PLEDGE_SAFE,     COLOR_POSITIVE, f"安全 {PLEDGE_SAFE}%"),
            ]:
                fig_r.add_hline(y=thresh, line_color=color, line_dash="dash",
                                 annotation_text=label, annotation_position="right")
            fig_r.update_layout(**PLOTLY_LAYOUT, yaxis_title="維持率 %", height=280)
            plotly_chart(fig_r)


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════
if not has_users():
    show_setup()
elif not st.session_state.get("authenticated"):
    show_login()
else:
    render_dashboard()
