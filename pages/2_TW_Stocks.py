import streamlit as st
import plotly.graph_objects as go
import pandas as pd

from config.settings import APP_NAME, APP_ICON, COLOR_POSITIVE, COLOR_NEGATIVE, PLOTLY_LAYOUT
from utils.auth import require_auth, logout
from utils.data_loader import load_tw_holdings
from utils.price_fetcher import fetch_current_prices, fetch_historical_prices
from utils.portfolio_calc import enrich_holdings

st.set_page_config(page_title=f"台股｜{APP_NAME}", page_icon="🇹🇼", layout="wide")
require_auth()

with st.sidebar:
    st.markdown(f"**👤 {st.session_state.username}**")
    if st.button("登出", use_container_width=True, key="logout_tw"):
        logout(); st.rerun()
    st.markdown("---")
    st.markdown("### 上傳台股 CSV")
    st.caption("格式：股票代號、股票名稱、庫存股數、平均成本、成本金額")
    uploaded = st.file_uploader("國泰證券台股 CSV", type=["csv"], key="tw_csv")
    if uploaded:
        from config.settings import TW_CSV_FILE
        TW_CSV_FILE.write_bytes(uploaded.getvalue())
        st.success("上傳成功！")
        st.cache_data.clear()
        st.rerun()

st.title("🇹🇼 台股持倉")

with st.spinner("載入台股資料..."):
    tw_holdings = load_tw_holdings()
    syms = tuple(h["symbol"] for h in tw_holdings)
    prices = fetch_current_prices(syms)
    tw_enriched = enrich_holdings(tw_holdings, prices, usd_twd=1.0)

# ── Summary metrics ──────────────────────────────────────────────────────────
total_cost = sum(h["cost_basis"] for h in tw_enriched)
total_value = sum(h["market_value"] or 0 for h in tw_enriched)
total_pnl = sum(h["unrealized_pnl"] or 0 for h in tw_enriched)
pnl_pct = total_pnl / total_cost * 100 if total_cost > 0 else 0

col1, col2, col3 = st.columns(3)
col1.metric("台股總成本", f"NT${total_cost:,.0f}")
col2.metric("台股市值",   f"NT${total_value:,.0f}",
            delta=f"NT${total_pnl:+,.0f} ({pnl_pct:+.2f}%)",
            delta_color="normal" if total_pnl >= 0 else "inverse")
col3.metric("持股數", f"{len(tw_enriched)} 檔")

st.markdown("---")

# ── Holdings detail table ─────────────────────────────────────────────────────
st.subheader("持倉明細")

rows = []
for h in tw_enriched:
    rows.append({
        "代號":       h["symbol"],
        "名稱":       h["name"],
        "庫存股數":   h["shares"],
        "成本均價":   f"NT${h['cost_per_share']:.2f}",
        "現價":       f"NT${h['current_price']:.2f}" if h["current_price"] else "—",
        "成本金額":   f"NT${h['cost_basis']:,.0f}",
        "現值":       f"NT${h['market_value']:,.0f}" if h["market_value"] else "—",
        "未實現損益": f"NT${h['unrealized_pnl']:+,.0f}" if h["unrealized_pnl"] is not None else "—",
        "損益率":     f"{h['pnl_pct']:+.2f}%" if h["pnl_pct"] is not None else "—",
    })

st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.markdown("---")

# ── Individual stock price history ───────────────────────────────────────────
st.subheader("個股走勢")

selected = st.selectbox("選擇股票", [f"{h['symbol']} {h['name']}" for h in tw_enriched])
sym = selected.split()[0]

period_map = {"3 個月": 90, "6 個月": 180, "1 年": 365}
period_label = st.radio("時間範圍", list(period_map.keys()), horizontal=True)
days = period_map[period_label]

hist_df = fetch_historical_prices(sym, days)

if not hist_df.empty:
    holding = next((h for h in tw_enriched if h["symbol"] == sym), None)
    cost_price = holding["cost_per_share"] if holding else None

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=hist_df.index, y=hist_df[sym],
        mode="lines", name="收盤價",
        line=dict(color="#4A90D9", width=2),
    ))

    if cost_price:
        fig.add_hline(
            y=cost_price,
            line_dash="dash", line_color="#FFB74D",
            annotation_text=f"成本 NT${cost_price:.2f}",
            annotation_position="bottom right",
        )

    # Color area based on profit/loss
    last_price = hist_df[sym].dropna().iloc[-1] if not hist_df.empty else None
    fill_color = "rgba(0,200,150,0.1)" if (last_price and cost_price and last_price >= cost_price) else "rgba(255,75,92,0.1)"
    fig.data[0].update(fill="tozeroy", fillcolor=fill_color)

    fig.update_layout(**PLOTLY_LAYOUT, yaxis_title="NT$", height=350)
    st.plotly_chart(fig, use_container_width=True)

    # Price change stats
    start_price = hist_df[sym].dropna().iloc[0]
    end_price = hist_df[sym].dropna().iloc[-1]
    chg = end_price - start_price
    chg_pct = chg / start_price * 100

    sc1, sc2, sc3 = st.columns(3)
    sc1.metric("起始價", f"NT${start_price:.2f}")
    sc2.metric("現價",   f"NT${end_price:.2f}")
    sc3.metric(f"{period_label}漲跌", f"NT${chg:+.2f} ({chg_pct:+.2f}%)",
               delta_color="normal" if chg >= 0 else "inverse")
else:
    st.warning(f"無法取得 {sym} 歷史價格，請稍後再試。")
