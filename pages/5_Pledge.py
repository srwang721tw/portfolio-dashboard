import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from datetime import date

from config.settings import (
    APP_NAME, APP_ICON, PLEDGE_CRITICAL, PLEDGE_WARNING, PLEDGE_SAFE,
    COLOR_POSITIVE, COLOR_NEGATIVE, COLOR_WARNING, PLOTLY_LAYOUT,
    TW_TICKERS, US_TICKERS,
)
from utils.auth import require_auth, logout
from utils.data_loader import load_pledge_config, save_pledge_config
from utils.price_fetcher import fetch_current_prices, fetch_usd_twd_rate, fetch_historical_prices, fetch_usd_twd_history
from utils.portfolio_calc import compute_pledge_ratio

st.set_page_config(page_title=f"質押監控｜{APP_NAME}", page_icon="🏦", layout="wide")
require_auth()

with st.sidebar:
    st.markdown(f"**👤 {st.session_state.username}**")
    if st.button("登出", use_container_width=True, key="logout_pledge"):
        logout(); st.rerun()

st.title("🏦 質押借款監控")
st.caption("維持率 140% 以下將觸發追繳（斷頭），請隨時注意。")

# ── Load config ───────────────────────────────────────────────────────────────
pledge_config = load_pledge_config()
loans = pledge_config.get("loans", [])

ALL_SYMBOLS = list(TW_TICKERS.keys()) + list(US_TICKERS.keys())

# ── Add/Edit loan UI ─────────────────────────────────────────────────────────
with st.expander("➕ 新增 / 管理質押設定", expanded=not loans):
    with st.form("add_pledge"):
        st.markdown("#### 新增質押筆")
        col_a, col_b = st.columns(2)
        with col_a:
            desc = st.text_input("說明（例：台股質押 A）", placeholder="台股質押")
            loan_amt = st.number_input("借款金額（TWD）", min_value=0, step=10000, value=500000)
            interest = st.number_input("年利率（%）", min_value=0.0, max_value=20.0, step=0.1, value=2.5)
            loan_date = st.date_input("借款日期", value=date.today())
        with col_b:
            st.markdown("**質押股票**（可多選）")
            pledged_syms = st.multiselect("股票代號", ALL_SYMBOLS)
            pledged_shares_input = {}
            for sym in pledged_syms:
                pledged_shares_input[sym] = st.number_input(
                    f"{sym} 質押股數", min_value=0, step=100, value=1000, key=f"shares_{sym}"
                )
                pledged_currency = st.selectbox(
                    f"{sym} 幣別", ["TWD", "USD"],
                    index=0 if sym in TW_TICKERS else 1,
                    key=f"ccy_{sym}",
                )

        submitted = st.form_submit_button("新增質押", use_container_width=True)
        if submitted and loan_amt > 0 and pledged_syms:
            pledged_stocks = [
                {
                    "symbol": sym,
                    "shares": pledged_shares_input.get(sym, 0),
                    "currency": "TWD" if sym in TW_TICKERS else "USD",
                }
                for sym in pledged_syms
            ]
            loans.append({
                "id": len(loans) + 1,
                "description": desc or f"質押 {len(loans)+1}",
                "pledged_stocks": pledged_stocks,
                "loan_amount_twd": loan_amt,
                "interest_rate": interest,
                "date": str(loan_date),
            })
            save_pledge_config({"loans": loans})
            st.success("質押設定已儲存！")
            st.rerun()

    if loans:
        st.markdown("---")
        st.markdown("#### 刪除質押筆")
        del_id = st.selectbox("選擇要刪除的質押", [f"#{l['id']} {l['description']}" for l in loans])
        if st.button("刪除", type="secondary"):
            del_num = int(del_id.split()[0].replace("#", ""))
            loans = [l for l in loans if l["id"] != del_num]
            save_pledge_config({"loans": loans})
            st.success("已刪除。"); st.rerun()


# ── Dashboard ─────────────────────────────────────────────────────────────────
if not loans:
    st.info("尚無質押設定，請點上方「新增」按鈕新增。")
    st.stop()

with st.spinner("計算維持率..."):
    all_pledged_syms = tuple({s["symbol"] for loan in loans for s in loan["pledged_stocks"]})
    prices = fetch_current_prices(all_pledged_syms)
    usd_twd = fetch_usd_twd_rate()

for loan in loans:
    st.markdown(f"### 📋 {loan['description']}")

    ratio, pledged_value = compute_pledge_ratio(
        loan["pledged_stocks"], prices, loan["loan_amount_twd"], usd_twd
    )

    # Gauge
    if ratio is not None:
        if ratio >= PLEDGE_SAFE:
            bar_color = COLOR_POSITIVE
            status = "🟢 安全"
        elif ratio >= PLEDGE_WARNING:
            bar_color = "#4A90D9"
            status = "🔵 觀察"
        elif ratio >= PLEDGE_CRITICAL:
            bar_color = COLOR_WARNING
            status = "🟡 警告"
        else:
            bar_color = COLOR_NEGATIVE
            status = "🔴 危險！追繳風險"

        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=ratio,
            number={"suffix": "%", "font": {"size": 36, "color": "#E6EDF3"}},
            delta={"reference": PLEDGE_WARNING, "suffix": "%",
                   "increasing": {"color": COLOR_POSITIVE},
                   "decreasing": {"color": COLOR_NEGATIVE}},
            title={"text": f"維持率　{status}", "font": {"size": 16, "color": "#E6EDF3"}},
            gauge={
                "axis": {"range": [0, 500], "tickcolor": "#E6EDF3", "tickfont": {"color": "#E6EDF3"}},
                "bar": {"color": bar_color, "thickness": 0.25},
                "bgcolor": "rgba(0,0,0,0)",
                "bordercolor": "#30363D",
                "steps": [
                    {"range": [0, PLEDGE_CRITICAL],  "color": "rgba(255,75,92,0.3)"},
                    {"range": [PLEDGE_CRITICAL, PLEDGE_WARNING], "color": "rgba(255,183,77,0.2)"},
                    {"range": [PLEDGE_WARNING, PLEDGE_SAFE],     "color": "rgba(74,144,217,0.2)"},
                    {"range": [PLEDGE_SAFE, 500],                "color": "rgba(0,200,150,0.15)"},
                ],
                "threshold": {
                    "line": {"color": COLOR_NEGATIVE, "width": 4},
                    "thickness": 0.85,
                    "value": PLEDGE_CRITICAL,
                },
            },
        ))
        fig_gauge.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            font={"color": "#E6EDF3"},
            height=280,
            margin=dict(t=40, b=10, l=10, r=10),
        )

        col_gauge, col_info = st.columns([2, 1])
        with col_gauge:
            st.plotly_chart(fig_gauge, use_container_width=True)

        with col_info:
            st.metric("維持率", f"{ratio:.1f}%")
            st.metric("質押股票市值", f"NT${pledged_value:,.0f}")
            st.metric("借款金額",     f"NT${loan['loan_amount_twd']:,.0f}")
            st.metric("年利率",       f"{loan['interest_rate']:.2f}%")
            st.metric("借款日期",     loan["date"])

            # Margin call price
            total_shares_twd = sum(
                s["shares"] for s in loan["pledged_stocks"] if s.get("currency") == "TWD"
            )
            if total_shares_twd > 0:
                margin_call_total = loan["loan_amount_twd"] * (PLEDGE_CRITICAL / 100)
                mc_price = margin_call_total / total_shares_twd
                st.metric("台股追繳均價",  f"NT${mc_price:.2f}", help="跌至此均價將觸發追繳")

        # Alert banners
        if ratio < PLEDGE_CRITICAL:
            st.error(f"⚠️ 緊急！維持率 {ratio:.1f}% 低於 {PLEDGE_CRITICAL}%，請立即補繳或賣出股票！")
        elif ratio < PLEDGE_WARNING:
            st.warning(f"⚠️ 警告：維持率 {ratio:.1f}% 低於 {PLEDGE_WARNING}%，請注意風險。")
    else:
        st.warning("無法取得質押股票現價，請確認 API 連線。")

    # Pledged stocks table
    rows = []
    for ps in loan["pledged_stocks"]:
        sym = ps["symbol"]
        price = prices.get(sym)
        fx = usd_twd if ps.get("currency") == "USD" else 1.0
        val_twd = ps["shares"] * price * fx if price else None
        rows.append({
            "代號": sym, "股數": ps["shares"],
            "現價": f"{'$' if ps.get('currency')=='USD' else 'NT$'}{price:.2f}" if price else "—",
            "市值(TWD)": f"NT${val_twd:,.0f}" if val_twd else "—",
            "幣別": ps.get("currency", "TWD"),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.markdown("---")

# ── Historical ratio trend ────────────────────────────────────────────────────
st.subheader("質押維持率趨勢（模擬）")
st.caption("以目前持股數計算歷史各日的估算維持率")

if loans:
    first_loan = loans[0]
    days = 90

    hist_frames = {}
    for ps in first_loan["pledged_stocks"]:
        sym = ps["symbol"]
        df = fetch_historical_prices(sym, days)
        if not df.empty:
            hist_frames[sym] = df

    if hist_frames:
        import pandas as pd
        all_idx = None
        ratio_series = None
        usd_hist = fetch_usd_twd_history(days)

        for ps in first_loan["pledged_stocks"]:
            sym = ps["symbol"]
            if sym not in hist_frames:
                continue
            df = hist_frames[sym].copy()
            fx = usd_hist.reindex(df.index, method="ffill").fillna(usd_twd) if ps.get("currency") == "USD" else 1.0
            val = df[sym] * ps["shares"] * fx
            ratio_series = (val if ratio_series is None else ratio_series + val)

        if ratio_series is not None:
            ratio_pct = ratio_series / first_loan["loan_amount_twd"] * 100

            fig_ratio = go.Figure()
            fig_ratio.add_trace(go.Scatter(
                x=ratio_pct.index, y=ratio_pct.values,
                mode="lines", name="維持率 %",
                line=dict(color="#4A90D9", width=2),
            ))
            for thresh, color, label in [
                (PLEDGE_CRITICAL, COLOR_NEGATIVE,   f"斷頭線 {PLEDGE_CRITICAL}%"),
                (PLEDGE_WARNING,  COLOR_WARNING,     f"警戒線 {PLEDGE_WARNING}%"),
                (PLEDGE_SAFE,     COLOR_POSITIVE,    f"安全線 {PLEDGE_SAFE}%"),
            ]:
                fig_ratio.add_hline(
                    y=thresh, line_color=color, line_dash="dash",
                    annotation_text=label, annotation_position="right",
                )
            fig_ratio.update_layout(**PLOTLY_LAYOUT, yaxis_title="維持率 %", height=320)
            st.plotly_chart(fig_ratio, use_container_width=True)
