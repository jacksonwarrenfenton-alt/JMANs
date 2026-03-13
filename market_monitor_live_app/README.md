# Market Monitor Website

A deployable Streamlit website for tracking market breadth and themes across **US-traded stocks above $1B market cap**.

## What it does

- Pulls the live stock universe from Financial Modeling Prep using a market-cap and country screen
- Computes breadth thresholds across daily / weekly / monthly / quarterly / yearly windows
- Lets you click counts like **Up 4% today** or **Down 25% month** to reveal the exact stock list
- Includes theme tracking, sector/industry breakdowns, TradingView/Yahoo links, plus per-ticker chart/fundamentals/news tabs
- Refreshes cached data every 24 hours and supports a manual refresh button

## Screens included

- Breadth dashboard
- Theme tracker
- Ticker detail (Chart / Fundamentals / News)

## Setup

1. Create an API key at Financial Modeling Prep.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Export your key:

```bash
export FMP_API_KEY="your_key_here"
```

4. Run locally:

```bash
streamlit run app.py
```

## Deploy

### Streamlit Community Cloud

- Push this folder to GitHub
- In Streamlit Cloud, create a new app pointing to `app.py`
- Add `FMP_API_KEY` as a secret

### Render / Railway / Docker host

Use a start command like:

```bash
streamlit run app.py --server.port=$PORT --server.address=0.0.0.0
```

and add `FMP_API_KEY` in environment variables.

## Optional daily warm-up ping

If your host sleeps, you can use the GitHub Actions workflow in `.github/workflows/ping.yml` and point it at your deployed URL.

## Notes

- The universe is filtered to **US, actively traded, non-fund, non-ETF names with market cap > $1B**.
- Historical calculations are done in-app with Yahoo Finance price history, so the first load can take a while for very large universes.
- For heavier production use, swap the history layer to a bulk market-data source.
