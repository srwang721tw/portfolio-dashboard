import streamlit as st
import plotly.graph_objects as go
import pandas as pd

from config.settings import APP_NAME, APP_ICON, COLOR_POSITIVE, COLOR_NEGATIVE, PLOTLY_LAYOUT
from utils.auth import require_auth, logout
from utils.data_loader import load_us_holdings
from utils.price_fetcher import fetch_current_prices, fetch_historical_prices, fetch_usd_twd_rate
from utils.portfolio_calc import enrich_holdings

st.set_page_config(page_title=f"美股｜{APP_NAME}", page_icon="🇺🇸", layout="wide")
require_auth()

with st.sidebar:
    st.markdown(f"**👤 {st.session_state.username}**")
    if st.button("登出", use_container_width=True, key="logout_us"):
        logout(); st.rerun()
    st.markdown("---")
    st.markdown("### 上傳美股 CSV")
    st.caption("格式：Symbol、Name、Shares、Avg Cost (USD)、Total Cost (USD)")
    uploaded = st.file_uploader("國泰證券美股 CSV", type=["csv"], key="us_csv")
    if uploaded:
        from config.settings import US_CSV_FILE
        US_CSV_FILE.write_bytes(uploaded.getvalue())
        st.success("上傳成功！")
        st.cache_data.clear()
        st.rerun()

st.title("🇺🇸 美股持倉")

with st.spinner("載入美股資料..."):
    us_holdings = load_us_holdings()
    syms = tuple(h["symbol"] for h in us_holdings)
    prices = fetch_current_prices(syms)
    usd_twd = fetch_usd_twd_rate()
    us_enriched = enrich_holdings(us_holdings, prices, usd_twd)

# ── Summary metrics ──────────────────────────────────────────────────────────
total_cost_usd = sum(h["cost_basis"] for h in us_enriched)
total_value_usd = sum(h["market_value"] or 0 for h in us_enriched)
total_pnl_usd = sum(h["unrealized_pnl"] or 0 for h in us_enriched)
pnl_pct = total_pnl_usd / total_cost_usd * 100 if total_cost_usd > 0 else 0

col1, col2, col3, col4 = st.columns(4)
col1.metric("美股總成本",   f"USD ${total_cost_usd:,.0f}")
col2.metric("美股市值",     f"USD ${total_value_usd:,.0f}",
            delta=f"${total_pnl_usd:+,.0f} ({pnl_pct:+.2f}%)",
            delta_color="normal" if total_pnl_usd >= 0 else "inverse")
col3.metric("折合台幣市值", f"NT${total_value_usd * usd_twd:,.0f}")
col4.metric("匯率",         f"1 USD = {usd_twd:.2f} TWD")

st.markdown("---")

# ── Holdings detail table ─────────────────────────────────────────────────────
st.subheader("持倉明細")

rows = []
for h in us_enriched:
    rows.append({
        "代號":           h["symbol"],
        "名稱":           h["name"],
        "股數":           h["shares"],
        "成本均價(USD)":  f"${h['cost_per_share']:.2f}",
        "現價(USD)":      f"${h['current_price']:.2f}" if h["current_price"] else "—",
        "成本金額(USD)":  f"${h['cost_basis']:,.2f}",
        "現值(USD)":      f"${h['market_value']:,.2f}" if h["market_value"] else "—",
        "現值(TWD)":      f"NT${h['market_value_twd']:,.0f}" if h["market_value_twd"] else "—",
        "未實現損益(USD)": f"${h['unrealized_pnl']:+,.2f}" if h["unrealized_pnl"] is not None else "—",
        "損益率":         f"{h['pnl_pct']:+.2f}%" if h["pnl_pct"] is not None else "—",
    })

st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.markdown("---")

# ── Individual stock price history ───────────────────────────────────────────
st.subheader("個股走勢")

selected = st.selectbox("選擇股票", [f"{h['symbol']} {h['name']}" for h in us_enriched])
sym = selected.split()[0]

period_map = {"3 個月": 90, "6 個月": 180, "1 年": 365, "2 年": 730}
period_label = st.radio("時間範圍", list(period_map.keys()), horizontal=True)
days = period_map[period_label]

hist_df = fetch_historical_prices(sym, days)

if not hist_df.empty:
    holding = next((h for h in us_enriched if h["symbol"] == sym), None)
    cost_price = holding["cost_per_share"] if holding else None

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=hist_df.index, y=hist_df[sym],
        mode="lines", name="收盤價 (USD)",
        line=dict(color="#A855F7", width=2),
    ))

    if cost_price:
        fig.add_hline(
            y=cost_price,
            line_dash="dash", line_color="#FFB74D",
            annotation_text=f"成本 ${cost_price:.2f}",
            annotation_position="bottom right",
        )

    last_price = hist_df[sym].dropna().iloc[-1] if not hist_df.empty else None
    fill_color = "rgba(168,85,247,0.1)"
    fig.data[0].update(fill="tozeroy", fillcolor=fill_color)

    fig.update_layout(**PLOTLY_LAYOUT, yaxis_title="USD $", height=350)
    st.plotly_chart(fig, use_container_width=True)

    # Stats
    start_price = hist_df[sym].dropna().iloc[0]
    end_price = hist_df[sym].dropna().iloc[-1]
    chg = end_price - start_price
    chg_pct = chg / start_price * 100

    sc1, sc2, sc3 = st.columns(3)
    sc1.metric("起始價", f"${start_price:.2f}")
    sc2.metric("現價",   f"${end_price:.2f}")
    sc3.metric(f"{period_label}漲跌", f"${chg:+.2f} ({chg_pct:+.2f}%)",
               delta_color="normal" if chg >= 0 else "inverse")
else:
    st.warning(f"無法取得 {sym} 歷史價格，請稍後再試。")
