# Yahoo Market Monitor

Streamlit app for an interactive market-monitoring dashboard powered by Yahoo Finance via `yfinance`.

## What it does
- Tracks breadth counts for US-traded stocks above $1B market cap
- Supports S&P 500 and Nasdaq 100 presets too
- Clickable threshold metrics for:
  - Daily up/down 4%
  - Weekly up/down 13% and 25%
  - Monthly up/down 25% and 50%
  - Quarterly up/down 25% and 50%
  - Yearly up 100%
- Theme tracker with return columns
- Ticker detail with chart, fundamentals, and news
- Daily cache refresh plus manual refresh button

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud
1. Upload `app.py` and `requirements.txt` to your GitHub repo
2. Create a Streamlit app with main file path `app.py`
3. No paid FMP key is needed in this Yahoo version

## Notes
- This version uses Yahoo/yfinance data and is best-effort
- Yahoo data access through `yfinance` is generally used for research/personal use
- Large universes can take a bit to load because the app computes breadth across many tickers
