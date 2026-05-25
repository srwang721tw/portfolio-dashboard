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
from utils.history_manager import save_snapshot, get_pnl_change, history_to_dataframe

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title=APP_NAME, page_icon=APP_ICON,
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
    font-size: 0.9rem; font-weight: 600; color: #E6EDF3;
    margin: 14px 0 6px 0; padding-left: 8px; border-left: 3px solid #00C896;
}
.divider { border-bottom: 1px solid #30363D; margin: 8px 0 12px 0; }
</style>
""", unsafe_allow_html=True)

# ── Altair theme ──────────────────────────────────────────────────────────────
_AX = dict(labelColor="#C9D1D9", titleColor="#8B949E", gridColor="#21262D",
           domainColor="#30363D", tickColor="#30363D", labelFontSize=11, titleFontSize=11)
PALETTE = [COLOR_POSITIVE, COLOR_NEUTRAL, COLOR_PURPLE, COLOR_WARNING, COLOR_NEGATIVE]


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


def fmt(v, prefix="NT$"):
    if abs(v) >= 1_000_000:
        return f"{prefix}{v/1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"{prefix}{v/1_000:.1f}K"
    return f"{prefix}{v:,.0f}"


def dc(v):
    return "normal" if v >= 0 else "inverse"


# ═════════════════════════════════════════════════════════════════════════════
# AUTH — always show login first
# ═════════════════════════════════════════════════════════════════════════════
def show_auth():
    st.markdown("<div style='max-width:440px;margin:70px auto'>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='text-align:center;font-size:2.5rem'>{APP_ICON}</div>"
        f"<div style='text-align:center;font-size:1.4rem;font-weight:700;margin-bottom:20px'>{APP_NAME}</div>",
        unsafe_allow_html=True,
    )

    tab_login, tab_reg = st.tabs(["登入", "新增帳號"])

    with tab_login:
        with st.form("login"):
            uname = st.text_input("帳號", placeholder="Username")
            pw    = st.text_input("密碼", type="password", placeholder="Password")
            code  = st.text_input("驗證碼 (2FA)", placeholder="Google Authenticator 6 位數字", max_chars=6)
            ok    = st.form_submit_button("登入", use_container_width=True, type="primary")
        if ok:
            if not has_users():
                st.error("尚無帳號，請先到「新增帳號」分頁建立帳號。"); return
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

    with tab_reg:
        if not has_users():
            st.info("首次使用，請建立管理員帳號。")
        with st.form("register"):
            uname2 = st.text_input("帳號")
            email2 = st.text_input("Email（選填）", placeholder="用於帳號識別")
            pw2a   = st.text_input("密碼（至少 8 字元）", type="password")
            pw2b   = st.text_input("確認密碼", type="password")
            totp2  = st.checkbox("啟用 Google Authenticator 雙因子驗證", value=True)
            ok2    = st.form_submit_button("建立帳號", use_container_width=True, type="primary")
        if ok2:
            if not uname2 or not pw2a:
                st.error("帳號和密碼不能為空"); return
            if len(pw2a) < 8:
                st.error("密碼至少 8 個字元"); return
            if pw2a != pw2b:
                st.error("兩次密碼不一致"); return
            success, secret = create_user(uname2, pw2a, totp2, email2)
            if not success:
                st.error("帳號已存在"); return
            st.success(f"帳號 **{uname2}** 建立成功！")
            if totp2 and secret:
                st.markdown("#### 掃描 QR Code 綁定 Google Authenticator")
                c1, c2 = st.columns([1, 2])
                with c1:
                    st.image(get_totp_qr_bytes(uname2, secret), width=180)
                with c2:
                    st.code(secret)
                    st.caption("用 Google Authenticator / Authy 掃描，或手動輸入金鑰")
                st.warning("請先完成綁定，再切換到「登入」分頁登入。")

    st.markdown("</div>", unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════════════════════
# CHARTS
# ═════════════════════════════════════════════════════════════════════════════
def _chart_alloc(all_enriched):
    rows = [{"標的": h["name"], "市值": h["market_value_twd"] or 0,
             "市場": "🇹🇼 台股" if h["currency"] == "TWD" else "🇺🇸 美股"}
            for h in all_enriched if (h["market_value_twd"] or 0) > 0]
    if not rows:
        return None
    df = pd.DataFrame(rows)
    return (
        alt.Chart(df).mark_arc(innerRadius=48, padAngle=0.02).encode(
            theta=alt.Theta("市值:Q", stack=True),
            color=alt.Color("標的:N",
                            scale=alt.Scale(range=PALETTE[:len(df)]),
                            legend=alt.Legend(title=None, orient="right")),
            tooltip=["標的:N", alt.Tooltip("市值:Q", format=",.0f", title="NT$"),
                     alt.Tooltip("市場:N")],
        ).properties(height=260, title="持股配置")
    )


def _chart_market_split(tw_val, us_val):
    df = pd.DataFrame({"市場": ["🇹🇼 台股", "🇺🇸 美股"], "市值": [tw_val, us_val]})
    return (
        alt.Chart(df).mark_arc(innerRadius=60, padAngle=0.03).encode(
            theta=alt.Theta("市值:Q", stack=True),
            color=alt.Color("市場:N",
                            scale=alt.Scale(range=[COLOR_NEUTRAL, COLOR_PURPLE]),
                            legend=alt.Legend(title=None, orient="bottom")),
            tooltip=["市場:N", alt.Tooltip("市值:Q", format=",.0f", title="NT$")],
        ).properties(height=260, title="台股 / 美股")
    )


def _chart_pnl_bar(all_enriched):
    df = pd.DataFrame([{"標的": h["name"], "損益": h["unrealized_pnl_twd"] or 0}
                       for h in all_enriched]).sort_values("損益")
    df["color"] = df["損益"].apply(lambda x: COLOR_POSITIVE if x >= 0 else COLOR_NEGATIVE)
    return (
        alt.Chart(df).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3,
                                cornerRadiusBottomLeft=3, cornerRadiusBottomRight=3).encode(
            x=alt.X("標的:N", sort=None, title=None,
                    axis=alt.Axis(labelAngle=-30)),
            y=alt.Y("損益:Q", title="NT$"),
            color=alt.Color("color:N", scale=None, legend=None),
            tooltip=["標的:N", alt.Tooltip("損益:Q", format="+,.0f", title="NT$")],
        )
        + alt.Chart(pd.DataFrame([{"y": 0}])).mark_rule(
            color="rgba(255,255,255,0.2)", strokeDash=[4, 4]
        ).encode(y="y:Q")
    ).properties(height=260, title="各標的損益")


def _chart_snapshot(df_snap):
    df  = df_snap.reset_index()[["date", "total_pnl_twd"]].rename(columns={"total_pnl_twd": "損益"})
    lc  = COLOR_POSITIVE if df["損益"].iloc[-1] >= 0 else COLOR_NEGATIVE
    base = alt.Chart(df).encode(x=alt.X("date:T", title=None))
    return (
        base.mark_area(color=lc, opacity=0.1).encode(y=alt.Y("損益:Q", title="NT$"))
        + base.mark_line(color=lc, strokeWidth=2).encode(y="損益:Q")
        + alt.Chart(pd.DataFrame([{"y": 0}])).mark_rule(
            color="rgba(255,255,255,0.2)", strokeDash=[4, 4]).encode(y="y:Q")
    ).properties(height=200, title="損益快照趨勢")


def _chart_price(hist, sym, cost_price=None, line_color=COLOR_NEUTRAL, prefix="NT$"):
    if hist.empty:
        return None
    df   = hist.reset_index(); df.columns = ["date", "price"]
    base = alt.Chart(df).encode(x=alt.X("date:T", title=None))
    layers = [
        base.mark_area(color=line_color, opacity=0.08).encode(y=alt.Y("price:Q", title=prefix)),
        base.mark_line(color=line_color, strokeWidth=2).encode(y="price:Q"),
    ]
    if cost_price:
        layers.append(
            alt.Chart(pd.DataFrame([{"c": cost_price}])).mark_rule(
                color="#FFB74D", strokeDash=[6, 4], strokeWidth=1.5
            ).encode(y=alt.Y("c:Q", title=None),
                     tooltip=alt.Tooltip("c:Q", format=".2f", title="成本"))
        )
    return alt.layer(*layers).properties(height=300)


def _chart_pnl_trend(df):
    data = df.reset_index()[["date", "total_pnl_twd", "total_value_twd"]].rename(
        columns={"total_pnl_twd": "損益", "total_value_twd": "市值"})
    lc = COLOR_POSITIVE if data["損益"].iloc[-1] >= 0 else COLOR_NEGATIVE
    return (
        alt.layer(
            alt.Chart(data).mark_area(color=lc, opacity=0.12).encode(
                x=alt.X("date:T", title=None),
                y=alt.Y("損益:Q", title="未實現損益 NT$",
                        axis=alt.Axis(titleColor=lc, labelColor=lc))),
            alt.Chart(data).mark_line(color=lc, strokeWidth=2.5).encode(
                x="date:T", y="損益:Q"),
            alt.Chart(pd.DataFrame([{"y": 0}])).mark_rule(
                color="rgba(255,255,255,0.2)", strokeDash=[4, 4]).encode(y="y:Q"),
            alt.Chart(data).mark_line(color=COLOR_NEUTRAL, strokeWidth=1.5,
                                       strokeDash=[5, 5], opacity=0.6).encode(
                x="date:T",
                y=alt.Y("市值:Q", title="總市值 NT$",
                        axis=alt.Axis(titleColor=COLOR_NEUTRAL, labelColor=COLOR_NEUTRAL))),
        )
        .resolve_scale(y="independent")
        .properties(height=360, title="損益 & 市值趨勢")
    )


def _chart_daily_bar(series, title="每日損益"):
    df = series.reset_index(); df.columns = ["date", "pnl"]
    df["color"] = df["pnl"].apply(lambda x: COLOR_POSITIVE if x >= 0 else COLOR_NEGATIVE)
    return (
        alt.Chart(df).mark_bar(width={"band": 0.8}).encode(
            x=alt.X("date:T", title=None),
            y=alt.Y("pnl:Q", title="NT$"),
            color=alt.Color("color:N", scale=None, legend=None),
            tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"),
                     alt.Tooltip("pnl:Q", format="+,.0f")],
        )
        + alt.Chart(pd.DataFrame([{"y": 0}])).mark_rule(
            color="rgba(255,255,255,0.2)").encode(y="y:Q")
    ).properties(height=260, title=title)


def _chart_pledge_gauge(ratio):
    mx = 500
    zones = pd.DataFrame([
        {"x": 0,              "x2": PLEDGE_CRITICAL, "color": COLOR_NEGATIVE, "zone": f"追繳 < {PLEDGE_CRITICAL}%"},
        {"x": PLEDGE_CRITICAL,"x2": PLEDGE_WARNING,  "color": COLOR_WARNING,  "zone": f"警戒 {PLEDGE_CRITICAL}~{PLEDGE_WARNING}%"},
        {"x": PLEDGE_WARNING, "x2": PLEDGE_SAFE,     "color": COLOR_NEUTRAL,  "zone": f"觀察 {PLEDGE_WARNING}~{PLEDGE_SAFE}%"},
        {"x": PLEDGE_SAFE,    "x2": mx,              "color": COLOR_POSITIVE, "zone": f"安全 ≥ {PLEDGE_SAFE}%"},
    ])
    sc = alt.Scale(domain=[0, mx])
    val_df = pd.DataFrame([{"r": min(ratio, mx)}])
    return (
        alt.Chart(zones).mark_bar(height=44, opacity=0.45).encode(
            x=alt.X("x:Q", scale=sc, title="維持率 %"), x2="x2:Q",
            color=alt.Color("color:N", scale=None, legend=None), tooltip="zone:N")
        + alt.Chart(val_df).mark_rule(color="white", strokeWidth=3).encode(
            x=alt.X("r:Q", scale=sc))
        + alt.Chart(val_df).mark_text(dy=-36, fontSize=22, fontWeight="bold", color="white").encode(
            x=alt.X("r:Q", scale=sc), text=alt.Text("r:Q", format=".1f"))
    ).properties(height=100)


def _chart_ratio_history(ratio_pct):
    df = ratio_pct.reset_index(); df.columns = ["date", "ratio"]
    rules_df = pd.DataFrame([
        {"y": PLEDGE_CRITICAL, "color": COLOR_NEGATIVE, "lbl": f"追繳 {PLEDGE_CRITICAL}%"},
        {"y": PLEDGE_WARNING,  "color": COLOR_WARNING,  "lbl": f"警戒 {PLEDGE_WARNING}%"},
        {"y": PLEDGE_SAFE,     "color": COLOR_POSITIVE, "lbl": f"安全 {PLEDGE_SAFE}%"},
    ])
    return (
        alt.Chart(df).mark_line(color=COLOR_NEUTRAL, strokeWidth=2).encode(
            x=alt.X("date:T", title=None), y=alt.Y("ratio:Q", title="維持率 %"),
            tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"),
                     alt.Tooltip("ratio:Q", format=".1f")])
        + alt.Chart(rules_df).mark_rule(strokeDash=[6, 4]).encode(
            y="y:Q", color=alt.Color("color:N", scale=None, legend=None), tooltip="lbl:N")
    ).properties(height=260, title="近 90 天維持率趨勢")


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

    # ── Load data ─────────────────────────────────────────────────────────────
    tw_h = load_tw_holdings()
    us_h = load_us_holdings()
    all_syms = tuple(h["symbol"] for h in tw_h + us_h)
    with st.spinner(""):
        prices  = fetch_current_prices(all_syms)
        usd_twd = fetch_usd_twd_rate()
    tw_e    = enrich_holdings(tw_h, prices, usd_twd)
    us_e    = enrich_holdings(us_h, prices, usd_twd)
    summary = portfolio_summary(tw_e, us_e)
    if summary["total_value_twd"] > 0:
        save_snapshot(summary["total_value_twd"], summary["total_pnl_twd"], summary["pnl_pct"])

    pnl_1d  = get_pnl_change(1)
    pnl_7d  = get_pnl_change(7)
    pnl_30d = get_pnl_change(30)

    # ── Header ────────────────────────────────────────────────────────────────
    h1, h2 = st.columns([5, 1])
    with h1:
        st.markdown(
            f"<div style='font-size:1.4rem;font-weight:700'>{APP_ICON} {APP_NAME}</div>"
            f"<div style='font-size:0.78rem;color:#8B949E'>"
            f"更新：{datetime.now().strftime('%Y/%m/%d %H:%M')}　USD/TWD：{usd_twd:.2f}</div>",
            unsafe_allow_html=True)
    with h2:
        bc1, bc2 = st.columns(2)
        with bc1:
            if st.button("⟳", use_container_width=True, help="重新整理價格"):
                st.cache_data.clear(); st.rerun()
        with bc2:
            if st.button("👤", use_container_width=True, help="個人資料"):
                st.session_state._show_profile = not st.session_state.get("_show_profile", False)
                st.rerun()

    # ── Profile panel ─────────────────────────────────────────────────────────
    if st.session_state.get("_show_profile"):
        _profile_panel()

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ── KPIs ──────────────────────────────────────────────────────────────────
    pnl = summary["total_pnl_twd"]
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("總市值",      fmt(summary["total_value_twd"]))
    k2.metric("未實現損益",   fmt(pnl), f"{summary['pnl_pct']:+.2f}%", delta_color=dc(pnl))
    k3.metric("今日損益",     fmt(pnl_1d)  if pnl_1d  is not None else "—", delta_color=dc(pnl_1d  or 0))
    k4.metric("近 7 日損益",  fmt(pnl_7d)  if pnl_7d  is not None else "—", delta_color=dc(pnl_7d  or 0))
    k5.metric("近 30 日損益", fmt(pnl_30d) if pnl_30d is not None else "—", delta_color=dc(pnl_30d or 0))

    st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab1, tab2, tab3, tab4 = st.tabs(["📊 總覽", "💼 持倉", "📈 損益歷史", "🏦 質押監控"])
    with tab1: _tab_overview(tw_e, us_e, summary)
    with tab2: _tab_holdings(tw_h, us_h, tw_e, us_e, prices, usd_twd)
    with tab3: _tab_history(tw_h, us_h)
    with tab4: _tab_pledge(prices, usd_twd)


# ── Profile panel ─────────────────────────────────────────────────────────────
def _profile_panel():
    uname   = st.session_state.username
    profile = get_profile(uname)
    with st.container(border=True):
        st.markdown(f"#### 👤 帳號設定（{uname}）")
        c1, c2 = st.columns(2)
        with c1:
            with st.form("profile_form"):
                new_email = st.text_input("Email", value=profile.get("email", ""))
                new_pw    = st.text_input("新密碼（留空不更改）", type="password")
                new_pw2   = st.text_input("確認新密碼", type="password")
                if st.form_submit_button("儲存", type="primary"):
                    if new_pw and new_pw != new_pw2:
                        st.error("兩次密碼不一致")
                    else:
                        update_profile(uname,
                                       new_password=new_pw or None,
                                       new_email=new_email)
                        st.success("已更新！")
                        st.session_state._show_profile = False
                        st.rerun()
        with c2:
            st.caption("2FA 狀態")
            st.write("✅ 已啟用" if profile.get("totp_enabled") else "❌ 未啟用")
            st.caption("目前帳號")
            st.write(uname)
            if st.button("關閉", use_container_width=True):
                st.session_state._show_profile = False; st.rerun()
            if st.button("登出", use_container_width=True, type="secondary"):
                logout(); st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# TAB 1 — 總覽
# ═════════════════════════════════════════════════════════════════════════════
def _tab_overview(tw_e, us_e, summary):
    all_e = tw_e + us_e
    c1, c2, c3 = st.columns(3)
    with c1:
        ch = _chart_alloc(all_e)
        if ch: _render(ch)
    with c2:
        _render(_chart_market_split(summary["tw_value_twd"], summary["us_value_twd"]))
    with c3:
        _render(_chart_pnl_bar(all_e))

    st.markdown("<div class='section-title'>完整持倉</div>", unsafe_allow_html=True)
    _holdings_table(all_e)

    df_snap = history_to_dataframe()
    if not df_snap.empty and len(df_snap) > 1:
        st.markdown("<div class='section-title'>損益快照</div>", unsafe_allow_html=True)
        _render(_chart_snapshot(df_snap))


# ═════════════════════════════════════════════════════════════════════════════
# TAB 2 — 持倉（台股 + 美股合併）
# ═════════════════════════════════════════════════════════════════════════════
def _tab_holdings(tw_h, us_h, tw_e, us_e, prices, usd_twd):
    all_e = tw_e + us_e

    # Upload CSVs
    with st.expander("📂 上傳持股 CSV（國泰證券格式）"):
        uc1, uc2 = st.columns(2)
        with uc1:
            st.caption("**台股**：股票代號、股票名稱、庫存股數、平均成本、成本金額")
            up_tw = st.file_uploader("台股 CSV", type=["csv"], key="up_tw")
            if up_tw:
                TW_CSV_FILE.write_bytes(up_tw.getvalue())
                try:
                    from utils.gdrive import upload; upload(TW_CSV_FILE)
                except Exception: pass
                st.success("台股 CSV 已上傳"); st.cache_data.clear(); st.rerun()
        with uc2:
            st.caption("**美股**：Symbol、Name、Shares、Avg Cost (USD)、Total Cost (USD)")
            up_us = st.file_uploader("美股 CSV", type=["csv"], key="up_us")
            if up_us:
                US_CSV_FILE.write_bytes(up_us.getvalue())
                try:
                    from utils.gdrive import upload; upload(US_CSV_FILE)
                except Exception: pass
                st.success("美股 CSV 已上傳"); st.cache_data.clear(); st.rerun()

    # Summary metrics — all in one row
    total_cost  = sum(h["cost_basis_twd"]          for h in all_e)
    total_value = sum(h["market_value_twd"]  or 0  for h in all_e)
    total_pnl   = sum(h["unrealized_pnl_twd"] or 0 for h in all_e)
    pnl_pct     = total_pnl / total_cost * 100 if total_cost > 0 else 0

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("總成本",        fmt(total_cost))
    m2.metric("總市值(TWD)",   fmt(total_value),
              f"{fmt(total_pnl)} ({pnl_pct:+.2f}%)", delta_color=dc(total_pnl))
    m3.metric("台股市值",       fmt(sum(h["market_value_twd"] or 0 for h in tw_e)))
    m4.metric("美股市值(USD)",  fmt(sum(h["market_value"]  or 0 for h in us_e), prefix="$"))
    m5.metric("匯率",           f"1 USD = {usd_twd:.2f}")

    # Holdings table
    st.markdown("<div class='section-title'>持倉明細</div>", unsafe_allow_html=True)
    _holdings_table(all_e)

    # Price history selector
    st.markdown("<div class='section-title'>個股走勢</div>", unsafe_allow_html=True)
    ca, cb, cc = st.columns([3, 1, 1])
    options = ([f"{h['symbol']} {h['name']}  🇹🇼" for h in tw_e]
               + [f"{h['symbol']} {h['name']}  🇺🇸" for h in us_e])
    sel    = ca.selectbox("股票", options, key="hold_sel")
    period = cb.radio("期間", ["3M","6M","1Y","2Y"], horizontal=True, key="hold_per")
    sym    = sel.split()[0]
    days   = {"3M":90,"6M":180,"1Y":365,"2Y":730}[period]
    is_us  = "🇺🇸" in sel
    h_obj  = next((h for h in all_e if h["symbol"] == sym), None)
    cost_p = h_obj["cost_per_share"] if h_obj else None
    lc     = COLOR_PURPLE if is_us else COLOR_NEUTRAL
    pref   = "$" if is_us else "NT$"
    hist   = fetch_historical_prices(sym, days)
    ch     = _chart_price(hist, sym, cost_p, lc, pref)
    if ch:
        _render(ch)
        with cc:
            st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
            if not hist.empty:
                s = hist[sym].dropna()
                chg = s.iloc[-1] - s.iloc[0]; chg_pct = chg / s.iloc[0] * 100
                st.metric("現價",     f"{pref}{s.iloc[-1]:.2f}")
                st.metric("期間漲跌", f"{chg_pct:+.2f}%", delta_color=dc(chg))
                if cost_p:
                    diff = s.iloc[-1] - cost_p
                    st.metric("vs 成本", f"{diff:+.2f}", delta_color=dc(diff))
    else:
        st.warning(f"無法取得 {sym} 歷史價格")


def _holdings_table(all_e):
    rows = [{
        "市場":    "🇹🇼 台股" if h["currency"] == "TWD" else "🇺🇸 美股",
        "代號":    h["symbol"],
        "名稱":    h["name"],
        "股數":    h["shares"],
        "成本均價": f"{h['cost_per_share']:.2f} {h['currency']}",
        "現價":    f"{h['current_price']:.2f}" if h["current_price"] else "—",
        "成本(TWD)": f"NT${h['cost_basis_twd']:,.0f}",
        "市值(TWD)": f"NT${h['market_value_twd']:,.0f}" if h["market_value_twd"] else "—",
        "損益(TWD)": f"NT${h['unrealized_pnl_twd']:+,.0f}" if h["unrealized_pnl_twd"] is not None else "—",
        "損益率":   f"{h['pnl_pct']:+.2f}%" if h["pnl_pct"] is not None else "—",
    } for h in all_e]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ═════════════════════════════════════════════════════════════════════════════
# TAB 3 — 損益歷史
# ═════════════════════════════════════════════════════════════════════════════
def _tab_history(tw_h, us_h):
    ca, cb = st.columns([4, 1])
    ca.markdown("<div class='section-title'>損益歷史分析</div>", unsafe_allow_html=True)
    days = cb.selectbox("回溯", [90, 180, 365], format_func=lambda x: f"{x} 天", key="hist_days")

    with st.spinner("計算歷史損益..."):
        tw_ph = {h["symbol"]: fetch_historical_prices(h["symbol"], days) for h in tw_h}
        us_ph = {h["symbol"]: fetch_historical_prices(h["symbol"], days) for h in us_h}
        usd_h = fetch_usd_twd_history(days)
        df    = compute_portfolio_history(tw_h, us_h, tw_ph, us_ph, usd_h, days)

    if df.empty:
        st.info("歷史資料不足，請確認 API 連線。"); return

    t1, t2, t3 = st.tabs(["📈 趨勢", "📊 每日 / 週", "📅 月度"])

    with t1:
        _render(_chart_pnl_trend(df))
        pnl_s = df["total_pnl_twd"].dropna()
        chg = pnl_s.iloc[-1] - pnl_s.iloc[0]
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("期間最高", fmt(pnl_s.max()))
        s2.metric("期間最低", fmt(pnl_s.min()))
        s3.metric("期間變化", fmt(chg), delta_color=dc(chg))
        s4.metric("損益率",   f"{df['pnl_pct'].iloc[-1]:+.2f}%")

    with t2:
        _render(_chart_daily_bar(df["daily_pnl_change"].dropna().tail(60), "近 60 日每日損益"))
        _render(_chart_daily_bar(df["daily_pnl_change"].dropna().resample("W").sum(), "每週損益"))

    with t3:
        monthly = df["daily_pnl_change"].dropna().resample("ME").sum()
        df_m = pd.DataFrame({"月份": monthly.index.strftime("%Y-%m"), "損益": monthly.values})
        df_m["color"] = df_m["損益"].apply(lambda x: COLOR_POSITIVE if x >= 0 else COLOR_NEGATIVE)
        ca2, cb2 = st.columns([3, 2])
        with ca2:
            _render(
                alt.Chart(df_m).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
                    x=alt.X("月份:N", sort=None, title=None, axis=alt.Axis(labelAngle=-30)),
                    y=alt.Y("損益:Q", title="NT$"),
                    color=alt.Color("color:N", scale=None, legend=None),
                    tooltip=["月份:N", alt.Tooltip("損益:Q", format="+,.0f")],
                ).properties(height=280, title="月度損益")
            )
        with cb2:
            pos   = (df_m["損益"] > 0).sum()
            neg   = (df_m["損益"] < 0).sum()
            best  = df_m.loc[df_m["損益"].idxmax()]
            worst = df_m.loc[df_m["損益"].idxmin()]
            st.markdown("<div class='section-title'>月度統計</div>", unsafe_allow_html=True)
            st.metric("獲利月",   f"{pos} 個月")
            st.metric("虧損月",   f"{neg} 個月")
            st.metric("月勝率",   f"{pos/max(pos+neg,1)*100:.1f}%")
            st.metric("最佳月份", best["月份"],  fmt(best["損益"]))
            st.metric("最差月份", worst["月份"], fmt(worst["損益"]))


# ═════════════════════════════════════════════════════════════════════════════
# TAB 4 — 質押監控
# ═════════════════════════════════════════════════════════════════════════════
def _tab_pledge(prices, usd_twd):
    from utils.gsheets import is_configured as sheets_ok, load_pledge_from_sheet, save_pledge_to_sheet, sheet_url

    # ── Load pledge data: Google Sheet > local JSON ───────────────────────────
    if sheets_ok():
        sheet_loans = load_pledge_from_sheet()
        pledge_cfg  = {"loans": sheet_loans} if sheet_loans else load_pledge_config()
    else:
        pledge_cfg = load_pledge_config()
    loans      = pledge_cfg.get("loans", [])
    ALL_SYMS   = list(TW_TICKERS.keys()) + list(US_TICKERS.keys())

    # ── Google Sheet status ───────────────────────────────────────────────────
    if sheets_ok():
        url = sheet_url()
        st.success(f"✅ 質押資料來自 Google Sheet　[開啟試算表]({url})")
    else:
        with st.expander("ℹ️ Google Sheets 尚未設定（點此展開說明）"):
            st.markdown("""
**設定步驟：**
1. 在 Google Cloud Console 啟用 **Google Sheets API**（同一個專案）
2. 建立一個 Google Sheet，第一個工作表重新命名為 `質押明細`
3. 將試算表分享給 Service Account Email（**編輯者**權限）
4. 從試算表 URL 複製 Sheet ID（`/spreadsheets/d/<ID>/edit`）
5. 在 Railway 新增環境變數：`GOOGLE_PLEDGE_SHEET_ID` = Sheet ID

**試算表欄位格式：**
`說明 | 借款金額TWD | 年利率% | 借款日期 | 質押代號 | 質押股數 | 幣別`

每筆質押的多檔股票各佔一行，說明/借款金額等重複填寫。
""")

    # ── Add / Delete loans ────────────────────────────────────────────────────
    with st.expander("➕ 新增 / 管理質押設定"):
        with st.form("add_pledge"):
            c1, c2 = st.columns(2)
            with c1:
                desc      = st.text_input("說明", placeholder="台股質押 A")
                loan_amt  = st.number_input("借款金額（TWD）", min_value=0, step=10000, value=500000)
                interest  = st.number_input("年利率（%）", 0.0, 20.0, 2.5, 0.1)
                loan_date = st.date_input("借款日期", value=date.today())
            with c2:
                p_syms   = st.multiselect("質押股票", ALL_SYMS)
                p_shares = {s: st.number_input(f"{s} 質押股數", 0, step=100, value=1000, key=f"ps_{s}")
                            for s in p_syms}
            if st.form_submit_button("新增", use_container_width=True, type="primary"):
                if loan_amt > 0 and p_syms:
                    new_id = (max(l["id"] for l in loans) + 1) if loans else 1
                    loans.append({
                        "id": new_id,
                        "description": desc or f"質押 {new_id}",
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
                    st.success("已新增"); st.rerun()

        if loans:
            del_c = st.selectbox("刪除", ["—"] + [f"#{l['id']} {l['description']}" for l in loans])
            if del_c != "—" and st.button("刪除選取"):
                del_id = int(del_c.split()[0].replace("#", ""))
                new_loans = [l for l in loans if l["id"] != del_id]
                save_pledge_config({"loans": new_loans})
                if sheets_ok():
                    save_pledge_to_sheet(new_loans)
                st.rerun()

    if not loans:
        st.info("尚無質押設定，請點上方「新增 / 管理質押設定」。"); return

    # ── Fetch prices for pledged stocks ───────────────────────────────────────
    p_syms_all   = tuple({s["symbol"] for loan in loans for s in loan["pledged_stocks"]})
    pledge_prices = fetch_current_prices(p_syms_all) if p_syms_all else {}

    # ── Render each loan ──────────────────────────────────────────────────────
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
            tw_s = sum(s["shares"] for s in loan["pledged_stocks"] if s.get("currency") == "TWD")
            if tw_s > 0:
                mc = loan["loan_amount_twd"] * PLEDGE_CRITICAL / 100
                st.metric("台股追繳均價", f"NT${mc/tw_s:.2f}", help="台股跌至此均價將觸發追繳")

        rows = [{
            "代號": ps["symbol"], "質押股數": ps["shares"],
            "現價": f"{'$' if ps.get('currency')=='USD' else 'NT$'}{pledge_prices[ps['symbol']]:.2f}"
                   if pledge_prices.get(ps["symbol"]) else "—",
            "市值(TWD)": fmt(ps["shares"] * pledge_prices[ps["symbol"]] *
                             (usd_twd if ps.get("currency") == "USD" else 1))
                         if pledge_prices.get(ps["symbol"]) else "—",
            "幣別": ps.get("currency", "TWD"),
        } for ps in loan["pledged_stocks"]]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.markdown("---")

    # ── Historical ratio chart ────────────────────────────────────────────────
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
if not st.session_state.get("authenticated"):
    show_auth()
else:
    render_dashboard()
