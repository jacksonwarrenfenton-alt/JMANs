import os
import math
import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Market Monitor", layout="wide")

FMP_API_KEY = os.getenv("FMP_API_KEY", "")
FMP_BASE = os.getenv("FMP_BASE_URL", "https://financialmodelingprep.com/stable")
MAX_UNIVERSE = int(os.getenv("MAX_UNIVERSE", "2500"))
PRICE_BATCH = int(os.getenv("PRICE_BATCH", "75"))

THRESHOLDS = {
    "Daily": [("Up 4%", 4, 1), ("Down 4%", -4, 1)],
    "Weekly": [("Up 13%", 13, 5), ("Up 25%", 25, 5), ("Down 13%", -13, 5), ("Down 25%", -25, 5)],
    "Monthly": [("Up 25%", 25, 21), ("Up 50%", 50, 21), ("Down 25%", -25, 21), ("Down 50%", -50, 21)],
    "Quarterly": [("Up 25%", 25, 63), ("Up 50%", 50, 63), ("Down 25%", -25, 63), ("Down 50%", -50, 63)],
    "Yearly": [("Up 100%", 100, 252), ("Down 50%", -50, 252)],
}

INDEX_OPTIONS = {
    "All US stocks > $1B": None,
    "NASDAQ only": ["NASDAQ"],
    "NYSE only": ["NYSE"],
    "AMEX only": ["AMEX"],
}


def dark_style():
    st.markdown(
        """
        <style>
        .stApp { background: #091627; color: #e6edf5; }
        div[data-testid="stMetricValue"] { color: #e6edf5; }
        div[data-testid="stMetricLabel"] { color: #99a9bf; }
        div.stButton > button {
            width: 100%;
            border: 1px solid #204f87;
            background: #10253f;
            color: #e6edf5;
            border-radius: 10px;
            min-height: 56px;
            font-weight: 600;
        }
        div.stButton > button:hover {
            border-color: #3b82f6;
            color: white;
        }
        .cell-card {
            background: #10253f;
            border: 1px solid #204f87;
            border-radius: 12px;
            padding: 12px;
            margin-bottom: 8px;
        }
        .small-muted { color: #94a3b8; font-size: 0.9rem; }
        .section-title { font-size: 1.4rem; font-weight: 700; margin: 0 0 0.5rem 0; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def fmp_get(path: str, params: Dict) -> List[Dict]:
    if not FMP_API_KEY:
        raise RuntimeError("Missing FMP_API_KEY environment variable.")
    params = dict(params)
    params["apikey"] = FMP_API_KEY
    resp = requests.get(f"{FMP_BASE}/{path}", params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("Error Message"):
        raise RuntimeError(data["Error Message"])
    return data


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def load_universe(index_choice: str) -> pd.DataFrame:
    exchanges = INDEX_OPTIONS[index_choice]
    rows: List[Dict] = []
    base_params = {
        "marketCapMoreThan": 1_000_000_000,
        "country": "US",
        "isEtf": "false",
        "isFund": "false",
        "isActivelyTrading": "true",
        "limit": MAX_UNIVERSE,
    }
    if exchanges is None:
        data = fmp_get("company-screener", base_params)
        rows.extend(data)
    else:
        for ex in exchanges:
            params = dict(base_params)
            params["exchange"] = ex
            rows.extend(fmp_get("company-screener", params))
    df = pd.DataFrame(rows).drop_duplicates(subset=["symbol"])
    if df.empty:
        return df
    keep = [
        "symbol", "companyName", "sector", "industry", "marketCap", "price", "exchangeShortName", "volume"
    ]
    for col in keep:
        if col not in df.columns:
            df[col] = None
    df = df[keep].rename(columns={"symbol": "ticker", "companyName": "name", "exchangeShortName": "exchange"})
    df["theme"] = df.apply(infer_theme, axis=1)
    return df.sort_values(["marketCap", "ticker"], ascending=[False, True]).reset_index(drop=True)


def infer_theme(row: pd.Series) -> str:
    text = f"{row.get('name','')} {row.get('sector','')} {row.get('industry','')}".lower()
    rules = [
        ("AI", ["artificial intelligence", "ai", "software", "data center"]),
        ("Semis", ["semiconductor", "chip", "gpu"]),
        ("Cybersecurity", ["cyber", "security"]),
        ("Cloud", ["cloud", "saas"]),
        ("Biotech", ["biotech", "pharma", "drug", "therapeutic"]),
        ("Crypto", ["bitcoin", "crypto", "blockchain", "miners"]),
        ("Defense", ["aerospace", "defense"]),
        ("Energy", ["oil", "gas", "solar", "renewable", "energy"]),
        ("Fintech", ["payments", "financial", "bank", "credit"]),
        ("E-commerce", ["retail", "e-commerce", "internet retail"]),
    ]
    for label, needles in rules:
        if any(n in text for n in needles):
            return label
    sector = row.get("sector") or "Other"
    return sector


@st.cache_data(ttl=60 * 60 * 24, show_spinner=True)
def load_price_history(tickers: Tuple[str, ...]) -> pd.DataFrame:
    closes: List[pd.DataFrame] = []
    ticker_list = list(tickers)
    for i in range(0, len(ticker_list), PRICE_BATCH):
        batch = ticker_list[i : i + PRICE_BATCH]
        data = yf.download(
            tickers=batch,
            period="420d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
        if data.empty:
            continue
        if len(batch) == 1:
            part = data[["Close"]].rename(columns={"Close": batch[0]})
        else:
            part = pd.DataFrame({t: data[t]["Close"] for t in batch if t in data.columns.get_level_values(0)})
        closes.append(part)
        time.sleep(0.15)
    if not closes:
        return pd.DataFrame()
    df = pd.concat(closes, axis=1)
    df = df.loc[:, ~df.columns.duplicated()]
    return df.dropna(how="all")


def compute_returns(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame()
    out = pd.DataFrame(index=history.columns)
    latest = history.ffill().iloc[-1]
    out["price"] = latest
    periods = {"daily": 1, "weekly": 5, "monthly": 21, "quarterly": 63, "yearly": 252}
    for label, days in periods.items():
        prev = history.ffill().iloc[-days - 1] if len(history) > days else history.ffill().iloc[0]
        out[label] = ((latest / prev) - 1) * 100
    out.index.name = "ticker"
    return out.reset_index()


def build_master(universe: pd.DataFrame, returns_df: pd.DataFrame) -> pd.DataFrame:
    master = universe.merge(returns_df, on="ticker", how="left")
    master["tradingview"] = master["ticker"].apply(lambda x: f"https://www.tradingview.com/symbols/{x}/")
    master["yahoo"] = master["ticker"].apply(lambda x: f"https://finance.yahoo.com/quote/{x}/")
    return master


def cohort_counts(master: pd.DataFrame) -> Dict[str, Dict[str, pd.DataFrame]]:
    mapping = {
        1: "daily",
        5: "weekly",
        21: "monthly",
        63: "quarterly",
        252: "yearly",
    }
    result: Dict[str, Dict[str, pd.DataFrame]] = {}
    for section, defs in THRESHOLDS.items():
        result[section] = {}
        for label, threshold, days in defs:
            col = mapping[days]
            if threshold >= 0:
                frame = master[master[col] >= threshold]
            else:
                frame = master[master[col] <= threshold]
            result[section][label] = frame.sort_values(col, ascending=False if threshold >= 0 else True)
    return result


def breadth_timeseries(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame()
    daily = history.ffill().pct_change() * 100
    weekly = history.ffill().pct_change(5) * 100
    monthly = history.ffill().pct_change(21) * 100
    quarterly = history.ffill().pct_change(63) * 100
    yearly = history.ffill().pct_change(252) * 100
    out = pd.DataFrame(index=history.index)
    out["Up 4% Today"] = (daily >= 4).sum(axis=1)
    out["Down 4% Today"] = (daily <= -4).sum(axis=1)
    out["Up 25% Month"] = (monthly >= 25).sum(axis=1)
    out["Down 25% Month"] = (monthly <= -25).sum(axis=1)
    out["Up 50% Quarter"] = (quarterly >= 50).sum(axis=1)
    out["Up 100% Year"] = (yearly >= 100).sum(axis=1)
    return out.dropna(how="all")


def render_overview(master: pd.DataFrame):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Universe", f"{len(master):,}")
    c2.metric("Themes", f"{master['theme'].nunique():,}")
    c3.metric("Median market cap", f"${master['marketCap'].median()/1e9:,.1f}B")
    c4.metric("Last refresh", datetime.now().strftime("%Y-%m-%d %H:%M"))


def render_breadth_tab(master: pd.DataFrame, cohorts: Dict[str, Dict[str, pd.DataFrame]], breadth_df: pd.DataFrame):
    st.markdown('<div class="section-title">Breadth Dashboard</div>', unsafe_allow_html=True)
    st.caption("Click any count to drill into the exact stocks behind that threshold.")

    selected_key = st.session_state.get("selected_cohort")

    for section, items in cohorts.items():
        st.subheader(section)
        cols = st.columns(len(items))
        for i, (label, frame) in enumerate(items.items()):
            with cols[i]:
                if st.button(f"{label}\n{len(frame):,}", key=f"btn_{section}_{label}"):
                    st.session_state["selected_cohort"] = (section, label)
                    selected_key = (section, label)

    if not breadth_df.empty:
        st.subheader("Breadth trend")
        trend = breadth_df.tail(90).reset_index().rename(columns={"index": "date"})
        fig = go.Figure()
        for col in ["Up 4% Today", "Down 4% Today", "Up 25% Month", "Down 25% Month"]:
            if col in trend.columns:
                fig.add_trace(go.Scatter(x=trend["date"], y=trend[col], mode="lines", name=col))
        fig.update_layout(template="plotly_dark", height=360, margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Latest heatmap")
        latest = breadth_df.tail(1).T.reset_index()
        latest.columns = ["Metric", "Count"]
        heat = px.imshow(
            [latest["Count"].tolist()],
            x=latest["Metric"].tolist(),
            y=["Latest"],
            aspect="auto",
            text_auto=True,
            color_continuous_scale="RdYlGn",
        )
        heat.update_layout(template="plotly_dark", height=220, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(heat, use_container_width=True)

    if selected_key:
        section, label = selected_key
        frame = cohorts[section][label].copy()
        st.subheader(f"{section} • {label}")
        if frame.empty:
            st.info("No stocks currently match this threshold.")
            return
        view = frame[[
            "ticker", "name", "price", "daily", "weekly", "monthly", "quarterly", "yearly",
            "theme", "sector", "industry", "marketCap", "tradingview", "yahoo"
        ]].rename(columns={
            "daily": "1D %", "weekly": "1W %", "monthly": "1M %", "quarterly": "1Q %", "yearly": "1Y %",
            "marketCap": "Market Cap"
        })
        st.dataframe(
            view,
            use_container_width=True,
            hide_index=True,
            column_config={
                "tradingview": st.column_config.LinkColumn("TradingView"),
                "yahoo": st.column_config.LinkColumn("Yahoo Finance"),
                "Market Cap": st.column_config.NumberColumn(format="$%.0f"),
                "price": st.column_config.NumberColumn("Price", format="$%.2f"),
                "1D %": st.column_config.NumberColumn(format="%.2f%%"),
                "1W %": st.column_config.NumberColumn(format="%.2f%%"),
                "1M %": st.column_config.NumberColumn(format="%.2f%%"),
                "1Q %": st.column_config.NumberColumn(format="%.2f%%"),
                "1Y %": st.column_config.NumberColumn(format="%.2f%%"),
            },
        )
        st.subheader("Sector / industry breakdown")
        b1, b2 = st.columns(2)
        with b1:
            st.dataframe(frame.groupby("sector").size().reset_index(name="Count").sort_values("Count", ascending=False), use_container_width=True, hide_index=True)
        with b2:
            st.dataframe(frame.groupby("industry").size().reset_index(name="Count").sort_values("Count", ascending=False).head(20), use_container_width=True, hide_index=True)


def render_theme_tracker(master: pd.DataFrame):
    st.markdown('<div class="section-title">Theme Tracker</div>', unsafe_allow_html=True)
    left, right = st.columns([2, 1])
    with left:
        themes = ["All"] + sorted(master["theme"].dropna().unique().tolist())
        theme = st.selectbox("Theme", themes)
    with right:
        sort_col = st.selectbox("Sort by", ["daily", "weekly", "monthly", "quarterly", "yearly", "marketCap"])

    filtered = master.copy()
    if theme != "All":
        filtered = filtered[filtered["theme"] == theme]
    filtered = filtered.sort_values(sort_col, ascending=False)

    view = filtered[["ticker", "name", "theme", "sector", "industry", "marketCap", "price", "daily", "weekly", "monthly", "quarterly", "yearly"]]
    st.dataframe(
        view,
        use_container_width=True,
        hide_index=True,
        column_config={
            "marketCap": st.column_config.NumberColumn("Market Cap", format="$%.0f"),
            "price": st.column_config.NumberColumn("Price", format="$%.2f"),
            "daily": st.column_config.NumberColumn("1D %", format="%.2f%%"),
            "weekly": st.column_config.NumberColumn("1W %", format="%.2f%%"),
            "monthly": st.column_config.NumberColumn("1M %", format="%.2f%%"),
            "quarterly": st.column_config.NumberColumn("1Q %", format="%.2f%%"),
            "yearly": st.column_config.NumberColumn("1Y %", format="%.2f%%"),
        },
    )


def render_stock_detail(master: pd.DataFrame):
    st.markdown('<div class="section-title">Ticker Detail</div>', unsafe_allow_html=True)
    ticker = st.selectbox("Pick a ticker", master["ticker"].tolist(), index=0)
    row = master[master["ticker"] == ticker].iloc[0]
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Price", f"${row['price']:.2f}" if pd.notna(row['price']) else "—")
    t2.metric("1D %", f"{row['daily']:.2f}%" if pd.notna(row['daily']) else "—")
    t3.metric("1M %", f"{row['monthly']:.2f}%" if pd.notna(row['monthly']) else "—")
    t4.metric("1Y %", f"{row['yearly']:.2f}%" if pd.notna(row['yearly']) else "—")

    chart_tab, fundamentals_tab, news_tab = st.tabs(["Chart", "Fundamentals", "News"])

    with chart_tab:
        hist = yf.Ticker(ticker).history(period="2y", auto_adjust=True)
        if not hist.empty:
            fig = go.Figure(go.Candlestick(
                x=hist.index,
                open=hist["Open"], high=hist["High"], low=hist["Low"], close=hist["Close"],
                name=ticker,
            ))
            fig.update_layout(template="plotly_dark", height=500, margin=dict(l=10, r=10, t=20, b=10))
            st.plotly_chart(fig, use_container_width=True)
        c1, c2 = st.columns(2)
        c1.link_button("TradingView", row["tradingview"])
        c2.link_button("Yahoo Finance", row["yahoo"])

    with fundamentals_tab:
        yt = yf.Ticker(ticker)
        info = yt.info or {}
        c1, c2, c3 = st.columns(3)
        c1.metric("Market cap", f"${info.get('marketCap', 0):,}" if info.get("marketCap") else "—")
        c2.metric("Forward PE", f"{info.get('forwardPE'):.2f}" if info.get("forwardPE") else "—")
        c3.metric("Revenue growth", f"{info.get('revenueGrowth')*100:.1f}%" if info.get("revenueGrowth") else "—")
        for title, frame in [
            ("Income statement", getattr(yt, "financials", pd.DataFrame())),
            ("Balance sheet", getattr(yt, "balance_sheet", pd.DataFrame())),
            ("Cash flow", getattr(yt, "cashflow", pd.DataFrame())),
        ]:
            st.markdown(f"**{title}**")
            if isinstance(frame, pd.DataFrame) and not frame.empty:
                st.dataframe(frame.head(12), use_container_width=True)
            else:
                st.info(f"No {title.lower()} data returned.")

    with news_tab:
        news = []
        try:
            news = yf.Ticker(ticker).news or []
        except Exception:
            news = []
        if not news:
            st.info("No recent news returned by provider.")
        for item in news[:12]:
            title = item.get("title", "Untitled")
            link = item.get("link") or item.get("canonicalUrl", {}).get("url")
            publisher = item.get("publisher", "Unknown")
            summary = item.get("summary", "")
            st.markdown(f"### [{title}]({link})" if link else f"### {title}")
            st.caption(publisher)
            if summary:
                st.write(summary)
            st.divider()


def build_app():
    dark_style()
    st.title("Interactive Market Monitor")
    st.caption("Daily-updating breadth + theme tracker for US-traded stocks above $1B market cap.")

    sidebar = st.sidebar
    sidebar.header("Controls")
    index_choice = sidebar.selectbox("Universe", list(INDEX_OPTIONS.keys()))
    if sidebar.button("Refresh data now"):
        st.cache_data.clear()
        st.rerun()
    sidebar.write("This app refreshes cached data every 24 hours.")

    try:
        with st.spinner("Loading live universe..."):
            universe = load_universe(index_choice)
        if universe.empty:
            st.error("No securities returned from the provider.")
            return
        with st.spinner("Downloading historical prices and computing breadth..."):
            history = load_price_history(tuple(universe["ticker"].tolist()))
            returns_df = compute_returns(history)
            master = build_master(universe, returns_df)
            cohorts = cohort_counts(master)
            breadth_df = breadth_timeseries(history)
    except Exception as exc:
        st.error(f"Data load failed: {exc}")
        st.info("Set FMP_API_KEY in your deployment environment, then rerun.")
        return

    render_overview(master)
    tab1, tab2, tab3 = st.tabs(["Breadth", "Theme Tracker", "Ticker Detail"])
    with tab1:
        render_breadth_tab(master, cohorts, breadth_df)
    with tab2:
        render_theme_tracker(master)
    with tab3:
        render_stock_detail(master)


if __name__ == "__main__":
    build_app()
