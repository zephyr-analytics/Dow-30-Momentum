# Dow 30 Momentum + Trend — Live Signals Dashboard

A local Plotly Dash page that shows, in real time, what the QuantConnect algorithm `dow30_momentum_trend.py` would hold, buy, sell or stop out of. It uses free Yahoo Finance data, so it needs no API keys.

---

## Setup

Requires Python 3.9 or newer; tested against the package versions for Python 3.14.

1. Put these files in one folder:
   - `dow30_signals_dash.py`
   - `requirements.txt`
   - `README.md`
2. Open a terminal in that folder. Optionally, create a virtual environment first:

   ```
   python -m venv .venv
   .venv\Scripts\activate          # Windows
   source .venv/bin/activate       # macOS / Linux
   ```

3. Install the packages:

   ```
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

## Run

```
python dow30_signals_dash.py
```

Then open **http://127.0.0.1:8050** in a browser. Stop the dashboard with `Ctrl + C` in the terminal.

The first load takes 10–30 seconds while it downloads two years of daily prices and five days of 30-minute prices for the Dow 30 and DIA.

---

## The strategy it follows

| Rule | Setting |
|---|---|
| **Trend** | 10-day SMA above the 200-day SMA **and** price above the 200-day SMA |
| **Momentum** | 21 / 63 / 126-day returns, ranked separately and the ranks averaged; the average return must be positive |
| **Holdings** | Top 10 qualifying stocks, equal weight. Each slot = leverage ÷ 10 of equity; empty slots stay in cash |
| **Leverage** | 2.5x by default (25% of equity per stock) |
| **Rebalance** | Weekly, **Monday 9:45 AM ET**, 15 minutes after the open (Tuesday if Monday is a holiday) |
| **Stop** | An hourly candle closing below the stock's 200-day SMA → sell it; it stays out until the next rebalance |
| **Market filter** | Sell **everything** if, on any hourly candle, **40% or fewer** of the Dow stocks qualify **or** DIA crosses below its 200-day SMA. Restart at the next rebalance once more than 40% qualify, even if DIA is still below its SMA |
| **Kalshi costs** | 0.02% fee per side; daily funding on positions; interest of 7% on margin backing positions at 1.5x+ and 3.5% on idle cash |

The daily SMAs and momentum use **completed** daily bars, like QuantConnect. Price is the latest intraday price. Hourly candles end on the hour (10:00 … 16:00 ET), also like QuantConnect.

---

## Using the page

### Inputs (top row)
The page remembers these between visits.

| Input | What it does |
|---|---|
| **Account equity ($)** | Your account size; used for dollar and share targets |
| **Leverage** | Total leverage across all holdings |
| **Top N** | How many stocks to hold (default 10) |
| **Kalshi funding (% per year)** | Enter the current funding rate from Kalshi's market page; positive = longs pay |
| **Current holdings** | Tickers you hold now, comma-separated (e.g. `NVDA, GS, CAT`); turns on stop alerts and the order list |
| **Refresh prices now** | Forces a new download; prices otherwise refresh every 5 minutes |

### Sections

**Cards**
- **Market filter:** ON, STOPPED or RISK OFF, with the current breadth and DIA versus its 200 SMA.
- **Next rebalance:** a countdown, and whether Monday will buy or stay in cash.
- **Qualifying stocks / Target exposure:** how many stocks pass and the total position size.
- **Kalshi carry:** estimated yearly interest minus funding, plus fees per round trip.

**Alerts:** the market filter tripping, and for each of your holdings either **STOPPED** (an hourly close below its 200 SMA since the last rebalance; stay out until Monday) or **OK**.

**Orders at the next rebalance:** BUY, BUY (re-enter), HOLD / resize or SELL for each stock, with dollar and share targets.

**Target portfolio:** the top-N stocks with rank, price, distance above the 200 SMA, momentum, weight, dollars and shares.

**Chart:** daily close with the 10 and 200 SMAs for any Dow stock or DIA.

**All Dow 30 stocks:** every stock's status, or the reason it doesn't qualify.

---

## Settings in the code

Edit the block at the top of `dow30_signals_dash.py` to keep it in line with your QuantConnect parameters:

```python
FAST, SLOW = 10, 200
MOM_LENS = (21, 63, 126)
MOM_METHOD = "rank"            # "rank" or "return"
MOM_POSITIVE = "blend"         # "blend", "all" or "off"
TOP_N = 10
DEFAULT_LEVERAGE = 2.5
MARKET_FILTER = "both"         # "both", "breadth", "sma" or "off"
BREADTH_MIN = 0.40
REBAL_WEEKDAY = 0              # 0 = Monday
REBAL_MINUTES_AFTER_OPEN = 15
DATA_REFRESH_SEC = 300
RISK_FREE = 0.04               # starting value for the funding box
PROMO_POS_APY, PROMO_CASH_APY, PROMO_MIN_LEV = 0.07, 0.035, 1.5
KALSHI_FEE = 0.0002
```

**Dow 30 tickers** are pulled from the SPDR DIA daily holdings file. If that fails, they come from Wikipedia, then from a built-in list. The subtitle shows which source was used.

---

## Notes and limits

- **Data delay.** Yahoo's free intraday data can lag by a few minutes, so check the price before placing orders.
- **Signals only.** The dashboard places no trades.
- **Stop replay.** Stops and market-filter trips are rebuilt from the last 5 days of hourly candles since the last rebalance. If the dashboard was off, it still catches any stop from earlier in the week.
- **Carry is an estimate.** Kalshi's funding is set by the market, and the 7% interest is a promotion with capacity caps that can change at any time.
- **Dashboard vs. backtest.** The dashboard uses today's Dow list; the backtest uses the point-in-time DIA holdings.

## Troubleshooting

| Problem | Fix |
|---|---|
| `pip` tries to build numpy / pandas / lxml from source | Run `python -m pip install --upgrade pip`, then install again |
| `ZoneInfoNotFoundError` on Windows | `pip install tzdata` |
| "Price download failed" or empty tables | Yahoo is rate-limiting or changed something: wait a minute and press Refresh, or run `pip install -U yfinance` |
| Tickers show "built-in list" | The SPDR and Wikipedia downloads both failed; the built-in list is the Dow as of June 29, 2026 |
| Leverage or Top N shows an old value | The page remembers inputs; retype the value |
| Port 8050 already in use | Close the other dashboard, or change `port=8050` at the bottom of the file |
