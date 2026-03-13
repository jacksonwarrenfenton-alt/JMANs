
import math
from datetime import datetime
from typing import Dict, List

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Yahoo Market Monitor", layout="wide")

US_EXCHANGES = ["NMS", "NYQ", "ASE", "BTS", "NGM", "NCM", "NMS"]

THEME_KEYWORDS = {
    "AI": ["artificial intelligence", "ai", "machine learning", "data center", "gpu"],
    "Semis": ["semiconductor", "chip", "foundry", "gpu", "memory"],
    "Cybersecurity": ["cyber", "security", "identity", "endpoint", "firewall"],
    "Cloud": ["cloud", "saas", "software", "platform", "devops"],
    "Defense": ["defense", "aerospace", "military"],
    "Energy": ["oil", "gas", "energy", "solar", "uranium", "nuclear"],
    "Biotech": ["biotech", "pharma", "therapeutic", "drug", "genomic"],
    "Crypto": ["bitcoin", "crypto", "blockchain", "mining"],
    "Consumer": ["retail", "e-commerce", "consumer", "restaurant"],
}

BREADTH_DEFS = [
    ("Daily Up 4%", "ret_1d", 4),
    ("Daily Down 4%", "ret_1d", -4),
    ("Weekly Up 13%", "ret_5d", 13),
    ("Weekly Down 13%", "ret_5d", -13),
    ("Weekly Up 25%", "ret_5d", 25),
    ("Weekly Down 25%", "ret_5d", -25),
    ("Monthly Up 25%", "ret_21d", 25),
    ("Monthly Down 25%", "ret_21d", -25),
    ("Monthly Up 50%", "ret_21d", 50),
    ("Monthly Down 50%", "ret_21d", -50),
    ("Quarterly Up 25%", "ret_63d", 25),
    ("Quarterly Down 25%", "ret_63d", -25),
    ("Quarterly Up 50%", "ret_63d", 50),
    ("Quarterly Down 50%", "ret_63d", -50),
    ("Yearly Up 100%", "ret_252d", 100),
]

@st.cache_data(ttl=86400, show_spinner=False)
def get_index_symbols(name: str) -> List[str]:
    try:
        if name == "S&P 500":
            table = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
            return sorted(table["Symbol"].astype(str).str.replace(".", "-", regex=False).unique().tolist())
        if name == "Nasdaq 100":
            table = pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100")[4]
            col = "Ticker" if "Ticker" in table.columns else "Ticker symbol"
            return sorted(table[col].astype(str).str.replace(".", "-", regex=False).unique().tolist())
    except Exception:
        return []
    return []

@st.cache_data(ttl=86400, show_spinner=True)
def screen_us_over_1b() -> pd.DataFrame:
    """Use Yahoo screener via yfinance to fetch a US equity universe above $1B market cap."""
    try:
        query = yf.EquityQuery(
            "and",
            [
                yf.EquityQuery("eq", ["region", "us"]),
                yf.EquityQuery("gte", ["intradaymarketcap", 1_000_000_000]),
            ],
        )
        rows = []
        size = 250
        for offset in range(0, 4000, size):
            res = yf.screen(query, offset=offset, size=size, sortField="intradaymarketcap", sortAsc=False)
            quotes = []
            if isinstance(res, dict):
                quotes = res.get("quotes") or res.get("finance", {}).get("result", [{}])[0].get("quotes", [])
            if not quotes:
                break
            for q in quotes:
                sym = q.get("symbol")
                if not sym:
                    continue
                rows.append(
                    {
                        "symbol": sym,
                        "name": q.get("shortName") or q.get("longName") or sym,
                        "exchange": q.get("exchange"),
                        "price": q.get("regularMarketPrice"),
                        "market_cap": q.get("intradayMarketCap") or q.get("marketCap"),
                    }
                )
            if len(quotes) < size:
                break
        df = pd.DataFrame(rows).drop_duplicates("symbol")
        if not df.empty:
            if "exchange" in df.columns:
                df = df[df["exchange"].fillna("").isin(US_EXCHANGES) | df["exchange"].isna()]
            df = df[df["market_cap"].fillna(0) >= 1_000_000_000]
        return df.sort_values("market_cap", ascending=False).reset_index(drop=True)
    except Exception:
        return pd.DataFrame(columns=["symbol", "name", "exchange", "price", "market_cap"])

@st.cache_data(ttl=86400, show_spinner=True)
def download_history(symbols: List[str]) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    chunks = [symbols[i:i+200] for i in range(0, len(symbols), 200)]
    close_frames = []
    for chunk in chunks:
        try:
            hist = yf.download(
                tickers=chunk,
                period="18mo",
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=True,
                group_by="ticker",
            )
        except Exception:
            continue
        if hist.empty:
            continue
        if isinstance(hist.columns, pd.MultiIndex):
            close = hist.xs("Close", axis=1, level=1, drop_level=False)
            close.columns = [c[0] for c in close.columns]
        else:
            # single symbol case
            close = hist[["Close"]].rename(columns={"Close": chunk[0]})
        close_frames.append(close)
    if not close_frames:
        return pd.DataFrame()
    close_df = pd.concat(close_frames, axis=1)
    close_df = close_df.loc[:, ~close_df.columns.duplicated()]
    return close_df

def compute_returns(close_df: pd.DataFrame) -> pd.DataFrame:
    latest = close_df.ffill().iloc[-1]
    out = pd.DataFrame({"symbol": close_df.columns, "price": latest.values})
    windows = {"ret_1d": 1, "ret_5d": 5, "ret_21d": 21, "ret_63d": 63, "ret_252d": 252}
    for col, w in windows.items():
        if len(close_df) > w:
            prev = close_df.ffill().iloc[-(w+1)]
            out[col] = ((latest / prev) - 1) * 100
        else:
            out[col] = np.nan
    return out

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_ticker_details(symbols: List[str]) -> pd.DataFrame:
    rows = []
    for sym in symbols[:250]:
        try:
            info = yf.Ticker(sym).info
        except Exception:
            info = {}
        rows.append(
            {
                "symbol": sym,
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "long_business_summary": info.get("longBusinessSummary", ""),
                "website": info.get("website", ""),
            }
        )
    return pd.DataFrame(rows)

def classify_theme(name: str, sector: str, industry: str, summary: str) -> str:
    blob = " ".join([str(name), str(sector), str(industry), str(summary)]).lower()
    for theme, keywords in THEME_KEYWORDS.items():
        if any(k in blob for k in keywords):
            return theme
    if sector:
        return sector
    return "Other"

def build_universe(choice: str) -> pd.DataFrame:
    if choice == "All US stocks > $1B":
        base = screen_us_over_1b()
    else:
        syms = get_index_symbols(choice)
        base = pd.DataFrame({"symbol": syms, "name": syms})
    if base.empty:
        return base
    close_df = download_history(base["symbol"].tolist())
    rets = compute_returns(close_df)
    merged = base.merge(rets, on="symbol", how="left")
    details = fetch_ticker_details(merged["symbol"].tolist())
    merged = merged.merge(details, on="symbol", how="left")
    merged["theme"] = merged.apply(
        lambda r: classify_theme(r.get("name", ""), r.get("sector", ""), r.get("industry", ""), r.get("long_business_summary", "")),
        axis=1,
    )
    merged["market_cap_b"] = merged["market_cap"].fillna(0) / 1_000_000_000
    merged["tradingview"] = merged["symbol"].apply(lambda s: f"https://www.tradingview.com/symbols/{s}/")
    merged["yahoo"] = merged["symbol"].apply(lambda s: f"https://finance.yahoo.com/quote/{s}")
    return merged

def threshold_subset(df: pd.DataFrame, col: str, threshold: float) -> pd.DataFrame:
    if threshold >= 0:
        subset = df[df[col] >= threshold]
    else:
        subset = df[df[col] <= threshold]
    return subset.sort_values(col, ascending=False if threshold >= 0 else True)

def render_metric_button(col_obj, label: str, count: int):
    if col_obj.button(f"{label}\n{count}", use_container_width=True):
        st.session_state["selected_metric"] = label

def metric_lookup(label: str):
    for item in BREADTH_DEFS:
        if item[0] == label:
            return item
    return None

def show_subset(df: pd.DataFrame, label: str):
    spec = metric_lookup(label)
    if not spec:
        return
    _, ret_col, threshold = spec
    subset = threshold_subset(df, ret_col, threshold).copy()
    st.subheader(label)
    if subset.empty:
        st.info("No tickers currently meet this threshold.")
        return
    keep = ["symbol", "name", "price", ret_col, "market_cap_b", "sector", "industry", "theme", "tradingview", "yahoo"]
    rename = {
        ret_col: "pct_change",
        "symbol": "Ticker",
        "name": "Name",
        "price": "Price",
        "market_cap_b": "Mkt Cap ($B)",
        "sector": "Sector",
        "industry": "Industry",
        "theme": "Theme",
        "tradingview": "TradingView",
        "yahoo": "Yahoo",
    }
    subset = subset[keep].rename(columns=rename)
    st.dataframe(
        subset,
        use_container_width=True,
        hide_index=True,
        column_config={
            "TradingView": st.column_config.LinkColumn("TradingView"),
            "Yahoo": st.column_config.LinkColumn("Yahoo"),
            "pct_change": st.column_config.NumberColumn("Change %", format="%.2f%%"),
            "Price": st.column_config.NumberColumn("Price", format="$%.2f"),
            "Mkt Cap ($B)": st.column_config.NumberColumn("Mkt Cap ($B)", format="%.2f"),
        },
    )
    sector_counts = subset["Sector"].replace("", "Unknown").value_counts().head(12)
    if not sector_counts.empty:
        st.caption("Sector breakdown")
        st.bar_chart(sector_counts)

def ticker_detail(df: pd.DataFrame):
    st.subheader("Ticker detail")
    options = df["symbol"].tolist()
    default = options[0] if options else None
    sym = st.selectbox("Select ticker", options, index=0 if default else None)
    if not sym:
        return
    row = df[df["symbol"] == sym].iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Price", f"${row['price']:.2f}" if pd.notna(row["price"]) else "—")
    c2.metric("1W", f"{row['ret_5d']:.2f}%" if pd.notna(row["ret_5d"]) else "—")
    c3.metric("1M", f"{row['ret_21d']:.2f}%" if pd.notna(row["ret_21d"]) else "—")
    c4.metric("YTD-ish (252d)", f"{row['ret_252d']:.2f}%" if pd.notna(row["ret_252d"]) else "—")
    chart_tab, fundamentals_tab, news_tab = st.tabs(["Chart", "Fundamentals", "News"])
    with chart_tab:
        hist = yf.Ticker(sym).history(period="1y", interval="1d", auto_adjust=True)
        if not hist.empty:
            st.line_chart(hist["Close"])
        else:
            st.info("No chart data returned.")
    with fundamentals_tab:
        st.write(f"**Sector:** {row.get('sector') or '—'}")
        st.write(f"**Industry:** {row.get('industry') or '—'}")
        st.write(f"**Theme:** {row.get('theme') or '—'}")
        if row.get("website"):
            st.write(f"**Website:** {row['website']}")
        summary = row.get("long_business_summary") or "No business summary available."
        st.write(summary)
    with news_tab:
        try:
            news = yf.Ticker(sym).news or []
        except Exception:
            news = []
        if not news:
            st.info("No recent news returned by Yahoo for this ticker.")
        for item in news[:10]:
            title = item.get("title") or "Untitled"
            url = item.get("link") or item.get("canonicalUrl", {}).get("url")
            publisher = item.get("publisher") or item.get("provider", {}).get("displayName", "")
            st.markdown(f"- [{title}]({url})" + (f" — {publisher}" if publisher else ""))

st.title("Interactive Market Monitor")
st.caption("Daily-updating breadth + theme tracker powered by Yahoo Finance/yfinance. Intended for research and educational use.")

with st.sidebar:
    st.header("Controls")
    universe_choice = st.selectbox("Universe", ["All US stocks > $1B", "S&P 500", "Nasdaq 100"])
    if st.button("Refresh data now", use_container_width=True):
        st.cache_data.clear()
        st.session_state.pop("selected_metric", None)
        st.rerun()
    st.write("Cached data refreshes every 24 hours.")
    st.write("For the all-US universe, Yahoo screener + market cap filters are used.")

with st.spinner("Loading market data..."):
    universe = build_universe(universe_choice)

if universe.empty:
    st.error("Could not load data from Yahoo Finance right now. Try Refresh data now, or switch to S&P 500 / Nasdaq 100.")
    st.stop()

# Top metrics
m1, m2, m3, m4 = st.columns(4)
m1.metric("Tickers", len(universe))
m2.metric("Themes", universe["theme"].nunique())
m3.metric("Median 1W", f"{universe['ret_5d'].median():.2f}%")
m4.metric("Median 1M", f"{universe['ret_21d'].median():.2f}%")

breadth_tab, themes_tab, detail_tab = st.tabs(["Breadth", "Theme tracker", "Ticker detail"])

with breadth_tab:
    st.subheader("Breadth thresholds")
    for i in range(0, len(BREADTH_DEFS), 3):
        cols = st.columns(3)
        for col_obj, spec in zip(cols, BREADTH_DEFS[i:i+3]):
            label, ret_col, threshold = spec
            cnt = len(threshold_subset(universe, ret_col, threshold))
            render_metric_button(col_obj, label, cnt)

    breadth_rows = []
    for label, ret_col, threshold in BREADTH_DEFS:
        breadth_rows.append({"Metric": label, "Count": len(threshold_subset(universe, ret_col, threshold))})
    breadth_df = pd.DataFrame(breadth_rows)
    st.dataframe(breadth_df, use_container_width=True, hide_index=True)

    chart_df = breadth_df.set_index("Metric")
    st.bar_chart(chart_df)

    selected_metric = st.session_state.get("selected_metric")
    if selected_metric:
        show_subset(universe, selected_metric)

with themes_tab:
    st.subheader("Theme tracker")
    theme_filter = st.multiselect("Themes", sorted(universe["theme"].dropna().unique().tolist()))
    theme_df = universe.copy()
    if theme_filter:
        theme_df = theme_df[theme_df["theme"].isin(theme_filter)]
    st.dataframe(
        theme_df[
            ["symbol", "name", "theme", "market_cap_b", "price", "ret_1d", "ret_5d", "ret_21d", "ret_63d", "ret_252d", "sector", "industry", "tradingview", "yahoo"]
        ].rename(
            columns={
                "symbol": "Ticker",
                "name": "Name",
                "theme": "Theme",
                "market_cap_b": "Mkt Cap ($B)",
                "price": "Price",
                "ret_1d": "Today %",
                "ret_5d": "1W %",
                "ret_21d": "1M %",
                "ret_63d": "3M %",
                "ret_252d": "1Y %",
                "sector": "Sector",
                "industry": "Industry",
                "tradingview": "TradingView",
                "yahoo": "Yahoo",
            }
        ),
        use_container_width=True,
        hide_index=True,
        column_config={
            "TradingView": st.column_config.LinkColumn("TradingView"),
            "Yahoo": st.column_config.LinkColumn("Yahoo"),
            "Price": st.column_config.NumberColumn("Price", format="$%.2f"),
            "Mkt Cap ($B)": st.column_config.NumberColumn("Mkt Cap ($B)", format="%.2f"),
            "Today %": st.column_config.NumberColumn("Today %", format="%.2f%%"),
            "1W %": st.column_config.NumberColumn("1W %", format="%.2f%%"),
            "1M %": st.column_config.NumberColumn("1M %", format="%.2f%%"),
            "3M %": st.column_config.NumberColumn("3M %", format="%.2f%%"),
            "1Y %": st.column_config.NumberColumn("1Y %", format="%.2f%%"),
        },
    )

    summary = (
        theme_df.groupby("theme")[["ret_1d", "ret_5d", "ret_21d", "ret_63d", "ret_252d"]]
        .median()
        .sort_values("ret_21d", ascending=False)
    )
    st.caption("Median performance by theme")
    st.dataframe(summary, use_container_width=True)

with detail_tab:
    ticker_detail(universe)
