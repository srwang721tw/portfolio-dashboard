import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import numpy as np

from config.settings import APP_NAME, APP_ICON, COLOR_POSITIVE, COLOR_NEGATIVE, COLOR_NEUTRAL, PLOTLY_LAYOUT
from utils.auth import require_auth, logout
from utils.data_loader import load_tw_holdings, load_us_holdings
from utils.price_fetcher import (
    fetch_current_prices, fetch_usd_twd_rate,
    fetch_historical_prices, fetch_usd_twd_history,
)
from utils.portfolio_calc import compute_portfolio_history
from utils.history_manager import history_to_dataframe

st.set_page_config(page_title=f"損益歷史｜{APP_NAME}", page_icon="📈", layout="wide")
require_auth()

with st.sidebar:
    st.markdown(f"**👤 {st.session_state.username}**")
    if st.button("登出", use_container_width=True, key="logout_hist"):
        logout(); st.rerun()
    st.markdown("---")
    days_option = st.selectbox("資料回溯期間", [90, 180, 365], index=1,
                                format_func=lambda x: f"{x} 天")

st.title("📈 損益歷史分析")

# ── Load data ─────────────────────────────────────────────────────────────────
with st.spinner("計算歷史損益..."):
    tw_holdings = load_tw_holdings()
    us_holdings = load_us_holdings()

    all_syms = tuple(
        [h["symbol"] for h in tw_holdings] +
        [h["symbol"] for h in us_holdings]
    )
    prices = fetch_current_prices(all_syms)
    usd_twd = fetch_usd_twd_rate()

    tw_price_hist = {h["symbol"]: fetch_historical_prices(h["symbol"], days_option) for h in tw_holdings}
    us_price_hist = {h["symbol"]: fetch_historical_prices(h["symbol"], days_option) for h in us_holdings}
    usd_twd_hist = fetch_usd_twd_history(days_option)

    df_hist = compute_portfolio_history(
        tw_holdings, us_holdings,
        tw_price_hist, us_price_hist, usd_twd_hist,
        days=days_option,
    )

    # Merge with stored snapshots if available
    df_snapshots = history_to_dataframe()

# ── Tab layout ────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["📈 損益趨勢", "📊 每日變化", "📅 月度摘要"])

# ─ Tab 1: P&L trend ──────────────────────────────────────────────────────────
with tab1:
    if df_hist.empty:
        st.info("歷史資料不足，請確認股票代號與 API 連線。")
    else:
        # Dual axis: portfolio value + P&L
        fig = go.Figure()

        last_pnl = df_hist["total_pnl_twd"].iloc[-1]
        line_color = COLOR_POSITIVE if last_pnl >= 0 else COLOR_NEGATIVE
        fill_color = "rgba(0,200,150,0.12)" if last_pnl >= 0 else "rgba(255,75,92,0.12)"

        fig.add_trace(go.Scatter(
            x=df_hist.index, y=df_hist["total_pnl_twd"],
            name="未實現損益 (TWD)", yaxis="y1",
            line=dict(color=line_color, width=2.5),
            fill="tozeroy", fillcolor=fill_color,
        ))
        fig.add_trace(go.Scatter(
            x=df_hist.index, y=df_hist["total_value_twd"],
            name="總市值 (TWD)", yaxis="y2",
            line=dict(color=COLOR_NEUTRAL, width=1.5, dash="dot"),
        ))
        fig.add_hline(y=0, line_color="rgba(255,255,255,0.3)", line_dash="dash")

        layout = dict(**PLOTLY_LAYOUT)
        layout.update(dict(
            yaxis=dict(title="未實現損益 TWD", gridcolor="#30363D", zerolinecolor="#30363D"),
            yaxis2=dict(title="總市值 TWD", overlaying="y", side="right", gridcolor="rgba(0,0,0,0)"),
            legend=dict(orientation="h", y=1.05),
            height=420,
        ))
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True)

        # Key stats
        c1, c2, c3, c4 = st.columns(4)
        pnl_series = df_hist["total_pnl_twd"].dropna()
        c1.metric("期間最高損益", f"NT${pnl_series.max():,.0f}")
        c2.metric("期間最低損益", f"NT${pnl_series.min():,.0f}")
        c3.metric("期間損益變化", f"NT${(pnl_series.iloc[-1] - pnl_series.iloc[0]):+,.0f}")
        c4.metric("損益率", f"{df_hist['pnl_pct'].iloc[-1]:+.2f}%")

# ─ Tab 2: Daily change bar chart ─────────────────────────────────────────────
with tab2:
    if df_hist.empty:
        st.info("歷史資料不足。")
    else:
        daily = df_hist["daily_pnl_change"].dropna().tail(60)
        colors = [COLOR_POSITIVE if v >= 0 else COLOR_NEGATIVE for v in daily]

        fig2 = go.Figure(go.Bar(
            x=daily.index, y=daily.values,
            marker_color=colors,
            text=[f"${v:,.0f}" for v in daily.values],
            textposition="outside",
            textfont=dict(size=9),
        ))
        fig2.add_hline(y=0, line_color="rgba(255,255,255,0.3)")
        fig2.update_layout(**PLOTLY_LAYOUT, yaxis_title="每日損益變化 TWD", height=380,
                            title="近 60 天每日損益變化")
        st.plotly_chart(fig2, use_container_width=True)

        # Weekly aggregated
        st.markdown("#### 週損益摘要")
        weekly = df_hist["daily_pnl_change"].dropna().resample("W").sum()
        weekly_df = pd.DataFrame({
            "週": weekly.index.strftime("%Y-W%U"),
            "週損益 (TWD)": weekly.values,
        })
        colors_w = [COLOR_POSITIVE if v >= 0 else COLOR_NEGATIVE for v in weekly_df["週損益 (TWD)"]]
        fig3 = go.Figure(go.Bar(
            x=weekly_df["週"], y=weekly_df["週損益 (TWD)"],
            marker_color=colors_w,
        ))
        fig3.update_layout(**PLOTLY_LAYOUT, height=280)
        st.plotly_chart(fig3, use_container_width=True)

# ─ Tab 3: Monthly summary ────────────────────────────────────────────────────
with tab3:
    if df_hist.empty:
        st.info("歷史資料不足。")
    else:
        monthly = df_hist["daily_pnl_change"].dropna().resample("ME").sum()
        monthly_df = pd.DataFrame({
            "月份": monthly.index.strftime("%Y-%m"),
            "月損益 (TWD)": monthly.values.round(0),
        })

        col_l, col_r = st.columns(2)
        with col_l:
            colors_m = [COLOR_POSITIVE if v >= 0 else COLOR_NEGATIVE for v in monthly_df["月損益 (TWD)"]]
            fig4 = go.Figure(go.Bar(
                x=monthly_df["月份"], y=monthly_df["月損益 (TWD)"],
                marker_color=colors_m,
                text=[f"NT${v:,.0f}" for v in monthly_df["月損益 (TWD)"]],
                textposition="outside",
            ))
            fig4.update_layout(**PLOTLY_LAYOUT, height=320, title="月損益")
            st.plotly_chart(fig4, use_container_width=True)

        with col_r:
            pos_months = (monthly_df["月損益 (TWD)"] > 0).sum()
            neg_months = (monthly_df["月損益 (TWD)"] < 0).sum()
            best_month = monthly_df.loc[monthly_df["月損益 (TWD)"].idxmax()]
            worst_month = monthly_df.loc[monthly_df["月損益 (TWD)"].idxmin()]

            st.markdown("#### 月度統計")
            st.metric("獲利月份", f"{pos_months} 個月")
            st.metric("虧損月份", f"{neg_months} 個月")
            st.metric("最佳月份", f"{best_month['月份']}",
                      delta=f"NT${best_month['月損益 (TWD)']:+,.0f}")
            st.metric("最差月份", f"{worst_month['月份']}",
                      delta=f"NT${worst_month['月損益 (TWD)']:+,.0f}",
                      delta_color="inverse")

            win_rate = pos_months / max(pos_months + neg_months, 1) * 100
            st.metric("月勝率", f"{win_rate:.1f}%")
