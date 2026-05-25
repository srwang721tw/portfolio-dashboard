import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from datetime import datetime

from config.settings import (
    APP_NAME, APP_ICON, COLOR_POSITIVE, COLOR_NEGATIVE, COLOR_NEUTRAL,
    COLOR_PURPLE, PLOTLY_LAYOUT,
)
from utils.auth import require_auth, logout
from utils.data_loader import load_tw_holdings, load_us_holdings
from utils.price_fetcher import fetch_current_prices, fetch_usd_twd_rate
from utils.portfolio_calc import enrich_holdings, portfolio_summary
from utils.history_manager import save_snapshot, get_pnl_change, history_to_dataframe

st.set_page_config(page_title=f"總覽｜{APP_NAME}", page_icon=APP_ICON, layout="wide")
require_auth()

with st.sidebar:
    st.markdown(f"**👤 {st.session_state.username}**")
    if st.button("登出", use_container_width=True, key="logout_overview"):
        logout(); st.rerun()


def fmt_twd(val: float) -> str:
    if abs(val) >= 1_000_000:
        return f"${val/1_000_000:.2f}M"
    elif abs(val) >= 1_000:
        return f"${val/1_000:.1f}K"
    return f"${val:,.0f}"


def delta_color(val: float) -> str:
    return "normal" if val >= 0 else "inverse"


# ── Load data ─────────────────────────────────────────────────────────────────
with st.spinner("載入資料中..."):
    tw_holdings = load_tw_holdings()
    us_holdings = load_us_holdings()

    tw_syms = tuple(h["symbol"] for h in tw_holdings)
    us_syms = tuple(h["symbol"] for h in us_holdings)
    all_syms = tw_syms + us_syms

    prices = fetch_current_prices(all_syms)
    usd_twd = fetch_usd_twd_rate()

    tw_enriched = enrich_holdings(tw_holdings, prices, usd_twd)
    us_enriched = enrich_holdings(us_holdings, prices, usd_twd)
    summary = portfolio_summary(tw_enriched, us_enriched)

# Save daily snapshot
if summary["total_value_twd"] > 0:
    save_snapshot(
        summary["total_value_twd"],
        summary["total_pnl_twd"],
        summary["pnl_pct"],
    )

# P&L period changes from stored history
pnl_1d = get_pnl_change(1)
pnl_7d = get_pnl_change(7)
pnl_30d = get_pnl_change(30)

# ── Page header ───────────────────────────────────────────────────────────────
st.title("📊 投資組合總覽")
now_str = datetime.now().strftime("%Y/%m/%d %H:%M")
st.caption(f"最後更新：{now_str}　匯率：1 USD = {usd_twd:.2f} TWD")

# ── KPI Metrics ───────────────────────────────────────────────────────────────
pnl = summary["total_pnl_twd"]
pnl_pct = summary["pnl_pct"]

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        "總市值（TWD）",
        fmt_twd(summary["total_value_twd"]),
        delta=f"{fmt_twd(pnl)} ({pnl_pct:+.2f}%)",
        delta_color=delta_color(pnl),
    )

with col2:
    d1_label = f"{fmt_twd(pnl_1d)}" if pnl_1d is not None else "N/A"
    st.metric("今日損益", d1_label,
              delta_color=delta_color(pnl_1d or 0) if pnl_1d else "off")

with col3:
    d7_label = f"{fmt_twd(pnl_7d)}" if pnl_7d is not None else "N/A"
    st.metric("近 7 日損益", d7_label,
              delta_color=delta_color(pnl_7d or 0) if pnl_7d else "off")

with col4:
    d30_label = f"{fmt_twd(pnl_30d)}" if pnl_30d is not None else "N/A"
    st.metric("近 30 日損益", d30_label,
              delta_color=delta_color(pnl_30d or 0) if pnl_30d else "off")

st.markdown("---")

# ── Charts row ────────────────────────────────────────────────────────────────
col_left, col_right = st.columns(2)

# Allocation donut by stock
with col_left:
    st.subheader("持股配置")
    all_enriched = tw_enriched + us_enriched
    alloc_data = [
        {"標的": h["name"], "市值（TWD）": h["market_value_twd"] or 0, "市場": "台股" if h["currency"] == "TWD" else "美股"}
        for h in all_enriched if (h["market_value_twd"] or 0) > 0
    ]
    if alloc_data:
        df_alloc = pd.DataFrame(alloc_data)
        fig_alloc = px.pie(
            df_alloc, names="標的", values="市值（TWD）",
            hole=0.45, color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig_alloc.update_layout(**PLOTLY_LAYOUT, showlegend=True,
                                 legend=dict(orientation="v", x=1.02))
        fig_alloc.update_traces(textinfo="percent+label", textfont_size=12)
        st.plotly_chart(fig_alloc, use_container_width=True)
    else:
        st.info("無法取得市值資料，請確認價格 API 是否可用。")

# Market split donut
with col_right:
    st.subheader("台股 / 美股比例")
    tw_val = summary["tw_value_twd"]
    us_val = summary["us_value_twd"]
    if tw_val + us_val > 0:
        fig_mkt = go.Figure(go.Pie(
            labels=["台股", "美股"],
            values=[tw_val, us_val],
            hole=0.5,
            marker_colors=[COLOR_NEUTRAL, COLOR_PURPLE],
            textinfo="label+percent",
            textfont_size=13,
        ))
        fig_mkt.update_layout(
            **PLOTLY_LAYOUT,
            annotations=[dict(
                text=f"總計<br>{fmt_twd(tw_val + us_val)}",
                x=0.5, y=0.5, font_size=13, showarrow=False, font_color="#E6EDF3",
            )],
        )
        st.plotly_chart(fig_mkt, use_container_width=True)

st.markdown("---")

# ── Holdings table ────────────────────────────────────────────────────────────
st.subheader("持股明細")

rows = []
for h in all_enriched:
    price_str = f"{h['current_price']:.2f}" if h["current_price"] else "—"
    pnl_str = f"{h['unrealized_pnl']:,.0f}" if h["unrealized_pnl"] is not None else "—"
    pnl_pct_str = f"{h['pnl_pct']:+.2f}%" if h["pnl_pct"] is not None else "—"
    rows.append({
        "標的": h["name"],
        "代號": h["symbol"],
        "市場": "🇹🇼 台股" if h["currency"] == "TWD" else "🇺🇸 美股",
        "股數": f"{h['shares']:,}",
        "成本均價": f"{h['cost_per_share']:.2f}",
        "現價": price_str,
        "市值": f"{h['market_value']:,.0f}" if h["market_value"] else "—",
        "未實現損益": pnl_str,
        "損益率": pnl_pct_str,
        "幣別": h["currency"],
    })

df_table = pd.DataFrame(rows)


def highlight_pnl(val):
    if "+" in str(val):
        return f"color: {COLOR_POSITIVE}"
    if val.startswith("-") if isinstance(val, str) else False:
        return f"color: {COLOR_NEGATIVE}"
    return ""


st.dataframe(
    df_table,
    use_container_width=True,
    hide_index=True,
    column_config={
        "損益率": st.column_config.TextColumn("損益率"),
    },
)

# ── P&L bar chart by stock ────────────────────────────────────────────────────
st.markdown("---")
st.subheader("各標的未實現損益")

pnl_rows = [
    {"標的": h["name"], "未實現損益（TWD）": h["unrealized_pnl_twd"] or 0}
    for h in all_enriched
]
df_pnl = pd.DataFrame(pnl_rows).sort_values("未實現損益（TWD）")

colors = [COLOR_POSITIVE if v >= 0 else COLOR_NEGATIVE for v in df_pnl["未實現損益（TWD）"]]
fig_bar = go.Figure(go.Bar(
    x=df_pnl["標的"],
    y=df_pnl["未實現損益（TWD）"],
    marker_color=colors,
    text=[f"${v:,.0f}" for v in df_pnl["未實現損益（TWD）"]],
    textposition="outside",
))
fig_bar.update_layout(**PLOTLY_LAYOUT, yaxis_title="TWD", height=320)
st.plotly_chart(fig_bar, use_container_width=True)

# ── Cumulative P&L history (from stored snapshots) ───────────────────────────
df_hist = history_to_dataframe()
if not df_hist.empty and len(df_hist) > 1:
    st.markdown("---")
    st.subheader("未實現損益趨勢（歷史快照）")
    fig_line = go.Figure()
    fig_line.add_trace(go.Scatter(
        x=df_hist.index,
        y=df_hist["total_pnl_twd"],
        mode="lines+markers",
        name="未實現損益",
        line=dict(color=COLOR_POSITIVE if df_hist["total_pnl_twd"].iloc[-1] >= 0 else COLOR_NEGATIVE, width=2),
        fill="tozeroy",
        fillcolor="rgba(0,200,150,0.1)" if df_hist["total_pnl_twd"].iloc[-1] >= 0 else "rgba(255,75,92,0.1)",
    ))
    fig_line.update_layout(**PLOTLY_LAYOUT, yaxis_title="TWD", height=300)
    st.plotly_chart(fig_line, use_container_width=True)
