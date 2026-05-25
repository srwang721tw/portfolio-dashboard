import streamlit as st
import altair as alt
import pandas as pd
import numpy as np
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
.stTabs [data-baseweb="tab-highlight"],
.stTabs [data-baseweb="tab-border"] { display: none; }

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
div[data-testid="stMetricValue"] > div { font-size: 1.35rem !important; }

.section-title {
    font-size: 0.9rem;
    font-weight: 600;
    color: #E6EDF3;
    margin: 14px 0 6px 0;
    padding-left: 8px;
    border-left: 3px solid #00C896;
}
</style>
""", unsafe_allow_html=True)

# ── Altair dark theme ─────────────────────────────────────────────────────────
_AXIS_CFG = dict(
    labelColor="#C9D1D9", titleColor="#8B949E",
    gridColor="#21262D", domainColor="#30363D",
    tickColor="#30363D", labelFontSize=11, titleFontSize=11,
)

def _render(chart, height: int = None):
    if height:
        chart = chart.properties(height=height)
    st.altair_chart(
        chart
        .configure(background="rgba(0,0,0,0)")
        .configure_view(strokeOpacity=0)
        .configure_axis(**_AXIS_CFG)
        .configure_legend(
            labelColor="#C9D1D9", titleColor="#8B949E",
            padding=8, cornerRadius=6, strokeColor="#30363D",
        )
        .configure_title(color="#E6EDF3", fontSize=13, fontWeight="normal"),
        use_container_width=True,
    )

# ── Formatters ────────────────────────────────────────────────────────────────
def fmt(v: float, prefix="NT$") -> str:
    if abs(v) >= 1_000_000:
        return f"{prefix}{v/1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"{prefix}{v/1_000:.1f}K"
    return f"{prefix}{v:,.0f}"

def dc(v: float) -> str:
    return "normal" if v >= 0 else "inverse"


# ═════════════════════════════════════════════════════════════════════════════
# AUTH
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
                st.caption("手動輸入金鑰，或用 Google Authenticator / Authy 掃描")
            st.warning("請先完成綁定再登入。")
    st.markdown("</div>", unsafe_allow_html=True)


def show_login():
    st.markdown("<div style='max-width:420px;margin:80px auto'>", unsafe_allow_html=True)
    st.markdown(f"<div style='text-align:center;font-size:2.5rem'>{APP_ICON}</div>", unsafe_allow_html=True)
    st.markdown(f"<div style='text-align:center;font-size:1.4rem;font-weight:700;margin-bottom:24px'>{APP_NAME}</div>", unsafe_allow_html=True)
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
# CHARTS
# ═════════════════════════════════════════════════════════════════════════════
PALETTE = [COLOR_POSITIVE, COLOR_NEUTRAL, COLOR_PURPLE, COLOR_WARNING, COLOR_NEGATIVE]


def _chart_alloc(all_enriched):
    rows = [{"標的": h["name"], "市值": h["market_value_twd"] or 0}
            for h in all_enriched if (h["market_value_twd"] or 0) > 0]
    if not rows:
        return None
    df = pd.DataFrame(rows)
    n = len(df)
    chart = alt.Chart(df).mark_arc(innerRadius=48, padAngle=0.02).encode(
        theta=alt.Theta("市值:Q", stack=True),
        color=alt.Color("標的:N",
                        scale=alt.Scale(range=PALETTE[:n]),
                        legend=alt.Legend(title=None, orient="right")),
        tooltip=[alt.Tooltip("標的:N"), alt.Tooltip("市值:Q", format=",.0f", title="NT$")],
    ).properties(height=260, title="持股配置")
    return chart


def _chart_split(tw_val, us_val):
    df = pd.DataFrame({"市場": ["🇹🇼 台股", "🇺🇸 美股"], "市值": [tw_val, us_val]})
    chart = alt.Chart(df).mark_arc(innerRadius=60, padAngle=0.03).encode(
        theta=alt.Theta("市值:Q", stack=True),
        color=alt.Color("市場:N",
                        scale=alt.Scale(range=[COLOR_NEUTRAL, COLOR_PURPLE]),
                        legend=alt.Legend(title=None, orient="bottom")),
        tooltip=[alt.Tooltip("市場:N"), alt.Tooltip("市值:Q", format=",.0f", title="NT$")],
    ).properties(height=260, title="台股 / 美股")
    return chart


def _chart_pnl_bar(all_enriched):
    rows = [{"標的": h["name"], "損益": h["unrealized_pnl_twd"] or 0}
            for h in all_enriched]
    df = pd.DataFrame(rows).sort_values("損益")
    df["color"] = df["損益"].apply(lambda x: COLOR_POSITIVE if x >= 0 else COLOR_NEGATIVE)
    chart = (
        alt.Chart(df).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3,
                                cornerRadiusBottomLeft=3, cornerRadiusBottomRight=3).encode(
            x=alt.X("標的:N", sort=None, title=None,
                    axis=alt.Axis(labelAngle=-30, labelColor="#C9D1D9")),
            y=alt.Y("損益:Q", title="NT$"),
            color=alt.Color("color:N", scale=None, legend=None),
            tooltip=[alt.Tooltip("標的:N"), alt.Tooltip("損益:Q", format="+,.0f", title="NT$")],
        )
        + alt.Chart(pd.DataFrame([{"y": 0}])).mark_rule(
            color="rgba(255,255,255,0.2)", strokeDash=[4, 4]
        ).encode(y="y:Q")
    ).properties(height=260, title="各標的損益")
    return chart


def _chart_snapshot(df_snap):
    df = df_snap.reset_index()[["date", "total_pnl_twd"]].rename(
        columns={"total_pnl_twd": "損益"}
    )
    lc = COLOR_POSITIVE if df["損益"].iloc[-1] >= 0 else COLOR_NEGATIVE
    base = alt.Chart(df).encode(x=alt.X("date:T", title=None))
    area = base.mark_area(color=lc, opacity=0.1).encode(y=alt.Y("損益:Q", title="NT$"))
    line = base.mark_line(color=lc, strokeWidth=2).encode(y="損益:Q")
    zero = alt.Chart(pd.DataFrame([{"y": 0}])).mark_rule(
        color="rgba(255,255,255,0.2)", strokeDash=[4, 4]
    ).encode(y="y:Q")
    return (area + line + zero).properties(height=200, title="損益快照")


def _chart_price(hist: pd.DataFrame, sym: str, cost_price=None,
                 line_color=COLOR_NEUTRAL, prefix="NT$"):
    if hist.empty:
        return None
    df = hist.reset_index()
    df.columns = ["date", "price"]
    base = alt.Chart(df).encode(x=alt.X("date:T", title=None))
    area = base.mark_area(color=line_color, opacity=0.08).encode(
        y=alt.Y("price:Q", title=prefix)
    )
    line = base.mark_line(color=line_color, strokeWidth=2).encode(y="price:Q")
    layers = [area, line]
    if cost_price:
        rule = alt.Chart(pd.DataFrame([{"c": cost_price}])).mark_rule(
            color="#FFB74D", strokeDash=[6, 4], strokeWidth=1.5
        ).encode(y=alt.Y("c:Q", title=None),
                 tooltip=alt.Tooltip("c:Q", format=".2f", title="成本"))
        layers.append(rule)
    return alt.layer(*layers).properties(height=300)


def _chart_pnl_trend(df):
    data = df.reset_index()[["date", "total_pnl_twd", "total_value_twd"]].rename(
        columns={"total_pnl_twd": "損益", "total_value_twd": "市值"}
    )
    lc = COLOR_POSITIVE if data["損益"].iloc[-1] >= 0 else COLOR_NEGATIVE

    pnl_area = alt.Chart(data).mark_area(color=lc, opacity=0.12).encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("損益:Q", title="未實現損益 NT$", axis=alt.Axis(titleColor=lc, labelColor=lc)),
    )
    pnl_line = alt.Chart(data).mark_line(color=lc, strokeWidth=2.5).encode(
        x="date:T", y="損益:Q"
    )
    zero = alt.Chart(pd.DataFrame([{"y": 0}])).mark_rule(
        color="rgba(255,255,255,0.2)", strokeDash=[4, 4]
    ).encode(y="y:Q")
    val_line = alt.Chart(data).mark_line(
        color=COLOR_NEUTRAL, strokeWidth=1.5, strokeDash=[5, 5], opacity=0.6
    ).encode(
        x="date:T",
        y=alt.Y("市值:Q", title="總市值 NT$",
                axis=alt.Axis(titleColor=COLOR_NEUTRAL, labelColor=COLOR_NEUTRAL)),
    )
    return (
        alt.layer(pnl_area, pnl_line, zero, val_line)
        .resolve_scale(y="independent")
        .properties(height=360, title="損益 & 市值趨勢")
    )


def _chart_daily_bar(daily: pd.Series, title="每日損益變化"):
    df = daily.reset_index()
    df.columns = ["date", "pnl"]
    df["color"] = df["pnl"].apply(lambda x: COLOR_POSITIVE if x >= 0 else COLOR_NEGATIVE)
    bar = alt.Chart(df).mark_bar(width={"band": 0.8}).encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("pnl:Q", title="NT$"),
        color=alt.Color("color:N", scale=None, legend=None),
        tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"), alt.Tooltip("pnl:Q", format="+,.0f")],
    )
    zero = alt.Chart(pd.DataFrame([{"y": 0}])).mark_rule(
        color="rgba(255,255,255,0.2)"
    ).encode(y="y:Q")
    return (bar + zero).properties(height=260, title=title)


def _chart_pledge_gauge(ratio: float):
    max_v = 500
    zones = pd.DataFrame([
        {"x": 0,   "x2": PLEDGE_CRITICAL,              "color": COLOR_NEGATIVE, "zone": f"追繳 < {PLEDGE_CRITICAL}%"},
        {"x": PLEDGE_CRITICAL, "x2": PLEDGE_WARNING,   "color": COLOR_WARNING,  "zone": f"警戒 {PLEDGE_CRITICAL}~{PLEDGE_WARNING}%"},
        {"x": PLEDGE_WARNING,  "x2": PLEDGE_SAFE,      "color": COLOR_NEUTRAL,  "zone": f"觀察 {PLEDGE_WARNING}~{PLEDGE_SAFE}%"},
        {"x": PLEDGE_SAFE,     "x2": max_v,            "color": COLOR_POSITIVE, "zone": f"安全 ≥ {PLEDGE_SAFE}%"},
    ])
    scale = alt.Scale(domain=[0, max_v])
    bg = alt.Chart(zones).mark_bar(height=44, opacity=0.45).encode(
        x=alt.X("x:Q", scale=scale, title="維持率 %"),
        x2="x2:Q",
        color=alt.Color("color:N", scale=None, legend=None),
        tooltip="zone:N",
    )
    val_df = pd.DataFrame([{"r": min(ratio, max_v)}])
    needle = alt.Chart(val_df).mark_rule(color="white", strokeWidth=3).encode(
        x=alt.X("r:Q", scale=scale)
    )
    label = alt.Chart(val_df).mark_text(dy=-36, fontSize=22, fontWeight="bold", color="white").encode(
        x=alt.X("r:Q", scale=scale),
        text=alt.Text("r:Q", format=".1f"),
    )
    return (bg + needle + label).properties(height=100)


def _chart_ratio_history(ratio_pct: pd.Series):
    df = ratio_pct.reset_index()
    df.columns = ["date", "ratio"]
    line = alt.Chart(df).mark_line(color=COLOR_NEUTRAL, strokeWidth=2).encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("ratio:Q", title="維持率 %"),
        tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"), alt.Tooltip("ratio:Q", format=".1f")],
    )
    rules_df = pd.DataFrame([
        {"y": PLEDGE_CRITICAL, "color": COLOR_NEGATIVE, "lbl": f"追繳 {PLEDGE_CRITICAL}%"},
        {"y": PLEDGE_WARNING,  "color": COLOR_WARNING,  "lbl": f"警戒 {PLEDGE_WARNING}%"},
        {"y": PLEDGE_SAFE,     "color": COLOR_POSITIVE, "lbl": f"安全 {PLEDGE_SAFE}%"},
    ])
    rules = alt.Chart(rules_df).mark_rule(strokeDash=[6, 4]).encode(
        y="y:Q",
        color=alt.Color("color:N", scale=None, legend=None),
        tooltip="lbl:N",
    )
    return (line + rules).properties(height=260, title="近 90 天維持率趨勢")


# ═════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═════════════════════════════════════════════════════════════════════════════
def render_dashboard():
    if not st.session_state.get("_gdrive_synced"):
        try:
            from utils.gdrive import sync_down_all
            sync_down_all()
        except Exception:
            pass
        st.session_state._gdrive_synced = True

    tw_holdings = load_tw_holdings()
    us_holdings = load_us_holdings()
    all_syms    = tuple(h["symbol"] for h in tw_holdings + us_holdings)

    with st.spinner(""):
        prices  = fetch_current_prices(all_syms)
        usd_twd = fetch_usd_twd_rate()

    tw_enriched = enrich_holdings(tw_holdings, prices, usd_twd)
    us_enriched = enrich_holdings(us_holdings, prices, usd_twd)
    summary     = portfolio_summary(tw_enriched, us_enriched)

    if summary["total_value_twd"] > 0:
        save_snapshot(summary["total_value_twd"], summary["total_pnl_twd"], summary["pnl_pct"])

    pnl_1d  = get_pnl_change(1)
    pnl_7d  = get_pnl_change(7)
    pnl_30d = get_pnl_change(30)
    now_str = datetime.now().strftime("%Y/%m/%d %H:%M")

    # ── Header ────────────────────────────────────────────────────────────────
    h1, h2 = st.columns([5, 1])
    with h1:
        st.markdown(
            f"<div style='font-size:1.4rem;font-weight:700'>{APP_ICON} {APP_NAME}</div>"
            f"<div style='font-size:0.78rem;color:#8B949E'>更新：{now_str}　USD/TWD：{usd_twd:.2f}</div>",
            unsafe_allow_html=True,
        )
    with h2:
        if st.button("⟳", use_container_width=True, help="重新整理價格"):
            st.cache_data.clear(); st.rerun()
        st.caption(f"👤 {st.session_state.username}")
        if st.button("登出", use_container_width=True):
            logout(); st.rerun()

    st.markdown("<div style='border-bottom:1px solid #30363D;margin:8px 0 12px 0'></div>",
                unsafe_allow_html=True)

    # ── KPIs ──────────────────────────────────────────────────────────────────
    pnl = summary["total_pnl_twd"]
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("總市值",      fmt(summary["total_value_twd"]))
    k2.metric("未實現損益",   fmt(pnl), f"{summary['pnl_pct']:+.2f}%", delta_color=dc(pnl))
    k3.metric("今日損益",     fmt(pnl_1d)  if pnl_1d  is not None else "—",
              delta_color=dc(pnl_1d  or 0))
    k4.metric("近 7 日損益",  fmt(pnl_7d)  if pnl_7d  is not None else "—",
              delta_color=dc(pnl_7d  or 0))
    k5.metric("近 30 日損益", fmt(pnl_30d) if pnl_30d is not None else "—",
              delta_color=dc(pnl_30d or 0))

    st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📊 總覽", "🇹🇼 台股", "🇺🇸 美股", "📈 損益歷史", "🏦 質押監控"
    ])
    with tab1: _tab_overview(tw_enriched, us_enriched, summary)
    with tab2: _tab_tw(tw_holdings, tw_enriched)
    with tab3: _tab_us(us_holdings, us_enriched, usd_twd)
    with tab4: _tab_history(tw_holdings, us_holdings)
    with tab5: _tab_pledge(prices, usd_twd)


# ═════════════════════════════════════════════════════════════════════════════
# TAB 1 — 總覽
# ═════════════════════════════════════════════════════════════════════════════
def _tab_overview(tw_enriched, us_enriched, summary):
    all_enriched = tw_enriched + us_enriched

    c1, c2, c3 = st.columns(3)
    with c1:
        ch = _chart_alloc(all_enriched)
        if ch: _render(ch)
    with c2:
        _render(_chart_split(summary["tw_value_twd"], summary["us_value_twd"]))
    with c3:
        _render(_chart_pnl_bar(all_enriched))

    st.markdown("<div class='section-title'>完整持倉</div>", unsafe_allow_html=True)
    rows = [{
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
    } for h in all_enriched]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    df_snap = history_to_dataframe()
    if not df_snap.empty and len(df_snap) > 1:
        st.markdown("<div class='section-title'>損益快照趨勢</div>", unsafe_allow_html=True)
        _render(_chart_snapshot(df_snap))


# ═════════════════════════════════════════════════════════════════════════════
# TAB 2 — 台股
# ═════════════════════════════════════════════════════════════════════════════
def _tab_tw(tw_holdings, tw_enriched):
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

    total_cost  = sum(h["cost_basis"] for h in tw_enriched)
    total_value = sum(h["market_value"] or 0 for h in tw_enriched)
    total_pnl   = sum(h["unrealized_pnl"] or 0 for h in tw_enriched)
    pnl_pct     = total_pnl / total_cost * 100 if total_cost > 0 else 0

    m1, m2, m3 = st.columns(3)
    m1.metric("台股成本", f"NT${total_cost:,.0f}")
    m2.metric("台股市值", f"NT${total_value:,.0f}",
              f"NT${total_pnl:+,.0f} ({pnl_pct:+.2f}%)", delta_color=dc(total_pnl))
    m3.metric("持股數",   f"{len(tw_enriched)} 檔")

    st.markdown("<div class='section-title'>持倉明細</div>", unsafe_allow_html=True)
    rows = [{
        "代號": h["symbol"], "名稱": h["name"], "庫存": h["shares"],
        "成本均價": f"NT${h['cost_per_share']:.2f}",
        "現價":     f"NT${h['current_price']:.2f}" if h["current_price"] else "—",
        "成本金額": f"NT${h['cost_basis']:,.0f}",
        "現值":     f"NT${h['market_value']:,.0f}" if h["market_value"] else "—",
        "損益":     f"NT${h['unrealized_pnl']:+,.0f}" if h["unrealized_pnl"] is not None else "—",
        "損益率":   f"{h['pnl_pct']:+.2f}%" if h["pnl_pct"] is not None else "—",
    } for h in tw_enriched]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.markdown("<div class='section-title'>個股走勢</div>", unsafe_allow_html=True)
    ca, cb = st.columns([3, 1])
    sel    = ca.selectbox("股票", [f"{h['symbol']} {h['name']}" for h in tw_enriched], key="tw_sel")
    period = cb.radio("期間", ["3M","6M","1Y"], horizontal=True, key="tw_per")
    sym    = sel.split()[0]
    days   = {"3M":90,"6M":180,"1Y":365}[period]
    hist   = fetch_historical_prices(sym, days)
    h_obj  = next((h for h in tw_enriched if h["symbol"] == sym), None)
    cost_p = h_obj["cost_per_share"] if h_obj else None
    ch     = _chart_price(hist, sym, cost_p, COLOR_NEUTRAL, "NT$")
    if ch:
        _render(ch)
        s = hist[sym].dropna()
        sc1, sc2, sc3 = st.columns(3)
        chg = s.iloc[-1] - s.iloc[0]; chg_pct = chg / s.iloc[0] * 100
        sc1.metric("期初價", f"NT${s.iloc[0]:.2f}")
        sc2.metric("現價",   f"NT${s.iloc[-1]:.2f}")
        sc3.metric("期間漲跌", f"NT${chg:+.2f} ({chg_pct:+.2f}%)", delta_color=dc(chg))
    else:
        st.warning(f"無法取得 {sym} 歷史價格")


# ═════════════════════════════════════════════════════════════════════════════
# TAB 3 — 美股
# ═════════════════════════════════════════════════════════════════════════════
def _tab_us(us_holdings, us_enriched, usd_twd):
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

    total_cost_usd  = sum(h["cost_basis"] for h in us_enriched)
    total_value_usd = sum(h["market_value"] or 0 for h in us_enriched)
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
        "代號": h["symbol"], "名稱": h["name"], "股數": h["shares"],
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
    ca, cb = st.columns([3, 1])
    sel    = ca.selectbox("股票", [f"{h['symbol']} {h['name']}" for h in us_enriched], key="us_sel")
    period = cb.radio("期間", ["3M","6M","1Y","2Y"], horizontal=True, key="us_per")
    sym    = sel.split()[0]
    days   = {"3M":90,"6M":180,"1Y":365,"2Y":730}[period]
    hist   = fetch_historical_prices(sym, days)
    h_obj  = next((h for h in us_enriched if h["symbol"] == sym), None)
    cost_p = h_obj["cost_per_share"] if h_obj else None
    ch     = _chart_price(hist, sym, cost_p, COLOR_PURPLE, "$")
    if ch:
        _render(ch)
        s = hist[sym].dropna()
        sc1, sc2, sc3 = st.columns(3)
        chg = s.iloc[-1] - s.iloc[0]; chg_pct = chg / s.iloc[0] * 100
        sc1.metric("期初價", f"${s.iloc[0]:.2f}")
        sc2.metric("現價",   f"${s.iloc[-1]:.2f}")
        sc3.metric("期間漲跌", f"${chg:+.2f} ({chg_pct:+.2f}%)", delta_color=dc(chg))
    else:
        st.warning(f"無法取得 {sym} 歷史價格")


# ═════════════════════════════════════════════════════════════════════════════
# TAB 4 — 損益歷史
# ═════════════════════════════════════════════════════════════════════════════
def _tab_history(tw_holdings, us_holdings):
    ca, cb = st.columns([4, 1])
    ca.markdown("<div class='section-title'>損益歷史分析</div>", unsafe_allow_html=True)
    days = cb.selectbox("回溯期間", [90, 180, 365],
                        format_func=lambda x: f"{x} 天", key="hist_days")

    with st.spinner("計算歷史損益..."):
        tw_ph = {h["symbol"]: fetch_historical_prices(h["symbol"], days) for h in tw_holdings}
        us_ph = {h["symbol"]: fetch_historical_prices(h["symbol"], days) for h in us_holdings}
        usd_h = fetch_usd_twd_history(days)
        df    = compute_portfolio_history(tw_holdings, us_holdings, tw_ph, us_ph, usd_h, days)

    if df.empty:
        st.info("歷史資料不足，請確認 API 連線。"); return

    t1, t2, t3 = st.tabs(["📈 趨勢", "📊 每日 / 週", "📅 月度"])

    with t1:
        _render(_chart_pnl_trend(df))
        pnl_s = df["total_pnl_twd"].dropna()
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("期間最高", fmt(pnl_s.max()))
        s2.metric("期間最低", fmt(pnl_s.min()))
        chg = pnl_s.iloc[-1] - pnl_s.iloc[0]
        s3.metric("期間變化", fmt(chg), delta_color=dc(chg))
        s4.metric("損益率",   f"{df['pnl_pct'].iloc[-1]:+.2f}%")

    with t2:
        daily  = df["daily_pnl_change"].dropna().tail(60)
        weekly = df["daily_pnl_change"].dropna().resample("W").sum()
        _render(_chart_daily_bar(daily, "近 60 日每日損益"))
        _render(_chart_daily_bar(weekly, "每週損益"))

    with t3:
        monthly = df["daily_pnl_change"].dropna().resample("ME").sum()
        df_m = pd.DataFrame({"月份": monthly.index.strftime("%Y-%m"), "損益": monthly.values})
        df_m["color"] = df_m["損益"].apply(lambda x: COLOR_POSITIVE if x >= 0 else COLOR_NEGATIVE)

        ca2, cb2 = st.columns([3, 2])
        with ca2:
            bar = alt.Chart(df_m).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
                x=alt.X("月份:N", sort=None, title=None, axis=alt.Axis(labelAngle=-30)),
                y=alt.Y("損益:Q", title="NT$"),
                color=alt.Color("color:N", scale=None, legend=None),
                tooltip=[alt.Tooltip("月份:N"), alt.Tooltip("損益:Q", format="+,.0f")],
            ).properties(height=280, title="月度損益")
            _render(bar)
        with cb2:
            pos  = (df_m["損益"] > 0).sum()
            neg  = (df_m["損益"] < 0).sum()
            best  = df_m.loc[df_m["損益"].idxmax()]
            worst = df_m.loc[df_m["損益"].idxmin()]
            st.markdown("<div class='section-title'>月度統計</div>", unsafe_allow_html=True)
            st.metric("獲利月",   f"{pos} 個月")
            st.metric("虧損月",   f"{neg} 個月")
            st.metric("月勝率",   f"{pos/max(pos+neg,1)*100:.1f}%")
            st.metric("最佳月份", best["月份"],  fmt(best["損益"]))
            st.metric("最差月份", worst["月份"], fmt(worst["損益"]))


# ═════════════════════════════════════════════════════════════════════════════
# TAB 5 — 質押監控
# ═════════════════════════════════════════════════════════════════════════════
def _tab_pledge(prices, usd_twd):
    pledge_cfg = load_pledge_config()
    loans      = pledge_cfg.get("loans", [])
    ALL_SYMS   = list(TW_TICKERS.keys()) + list(US_TICKERS.keys())

    with st.expander("➕ 新增 / 管理質押設定", expanded=not loans):
        with st.form("add_pledge"):
            c1, c2 = st.columns(2)
            with c1:
                desc      = st.text_input("說明", placeholder="台股質押 A")
                loan_amt  = st.number_input("借款金額（TWD）", min_value=0, step=10000, value=500000)
                interest  = st.number_input("年利率（%）", 0.0, 20.0, 2.5, 0.1)
                loan_date = st.date_input("借款日期", value=date.today())
            with c2:
                pledged_syms = st.multiselect("質押股票", ALL_SYMS)
                p_shares = {}
                for sym in pledged_syms:
                    p_shares[sym] = st.number_input(
                        f"{sym} 質押股數", 0, step=100, value=1000, key=f"ps_{sym}"
                    )
            if st.form_submit_button("新增", use_container_width=True, type="primary"):
                if loan_amt > 0 and pledged_syms:
                    new_id = (max(l["id"] for l in loans) + 1) if loans else 1
                    loans.append({
                        "id": new_id,
                        "description": desc or f"質押 {new_id}",
                        "pledged_stocks": [
                            {"symbol": s, "shares": p_shares[s],
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
        st.info("尚無質押設定。"); return

    pledged_syms_all = tuple({s["symbol"] for loan in loans for s in loan["pledged_stocks"]})
    pledge_prices    = fetch_current_prices(pledged_syms_all) if pledged_syms_all else {}

    for loan in loans:
        ratio, p_value = compute_pledge_ratio(
            loan["pledged_stocks"], pledge_prices, loan["loan_amount_twd"], usd_twd
        )
        st.markdown(f"### {loan['description']}")

        if ratio is None:
            st.warning("無法取得現價"); continue

        ga, gb = st.columns([3, 1])
        with ga:
            _render(_chart_pledge_gauge(ratio), height=100)
            if ratio < PLEDGE_CRITICAL:
                st.error(f"⚠️ 緊急！維持率 {ratio:.1f}% 已低於追繳線 {PLEDGE_CRITICAL}%！")
            elif ratio < PLEDGE_WARNING:
                st.warning(f"⚠️ 維持率 {ratio:.1f}% 低於警戒線 {PLEDGE_WARNING}%，請注意。")
        with gb:
            st.metric("維持率",      f"{ratio:.1f}%")
            st.metric("質押股票市值", fmt(p_value))
            st.metric("借款金額",     fmt(loan["loan_amount_twd"]))
            st.metric("年利率",       f"{loan['interest_rate']:.1f}%")
            tw_shares = sum(s["shares"] for s in loan["pledged_stocks"] if s.get("currency") == "TWD")
            if tw_shares > 0:
                mc_val = loan["loan_amount_twd"] * PLEDGE_CRITICAL / 100
                st.metric("台股追繳均價", f"NT${mc_val/tw_shares:.2f}",
                           help="台股跌至此均價將觸發追繳")

        # Pledged stocks table
        rows = [{
            "代號": ps["symbol"],
            "質押股數": ps["shares"],
            "現價": f"{pledge_prices.get(ps['symbol'], '—'):.2f}" if pledge_prices.get(ps['symbol']) else "—",
            "市值(TWD)": fmt(ps["shares"] * pledge_prices[ps["symbol"]] *
                             (usd_twd if ps.get("currency") == "USD" else 1))
                         if pledge_prices.get(ps["symbol"]) else "—",
            "幣別": ps.get("currency", "TWD"),
        } for ps in loan["pledged_stocks"]]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.markdown("---")

    # Historical ratio chart (first loan)
    first = loans[0]
    usd_h = fetch_usd_twd_history(90)
    ratio_s = None
    for ps in first["pledged_stocks"]:
        df_p = fetch_historical_prices(ps["symbol"], 90)
        if df_p.empty: continue
        fx = usd_h.reindex(df_p.index, method="ffill").fillna(usd_twd) \
             if ps.get("currency") == "USD" else 1.0
        val = df_p[ps["symbol"]] * ps["shares"] * fx
        ratio_s = val if ratio_s is None else ratio_s + val
    if ratio_s is not None:
        _render(_chart_ratio_history(ratio_s / first["loan_amount_twd"] * 100))


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════
if not has_users():
    show_setup()
elif not st.session_state.get("authenticated"):
    show_login()
else:
    render_dashboard()
