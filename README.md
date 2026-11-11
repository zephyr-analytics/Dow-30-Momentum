# Momentum + Trend live signals dashboards (Nasdaq-100 and Dow 30)

Two Plotly Dash dashboards that apply the QuantConnect momentum + trend algos to today's market, so you can see
this week's picks, the market filters and the orders to place.

| Dashboard | Run | Opens at | Market filter | Vol target |
|---|---|---|---|---|
| Nasdaq-100 (top 30) | `python nasdaq_dashboard.py` (or `run_nasdaq.bat`) | http://127.0.0.1:8050 | QQQ, checked hourly | 35% |
| Dow 30 | `python dow_dashboard.py` (or `run_dow.bat`) | http://127.0.0.1:8051 | DIA, checked at the close | off |

Both can run at the same time.

## Files

- `momentum_dashboard.py` - the shared engine: member lists, signals, orders, layout. All logic lives here.
- `nasdaq_dashboard.py`, `dow_dashboard.py` - launchers that pick the index and port.
- `run_nasdaq.bat`, `run_dow.bat` - Windows double-click launchers.
- `requirements.txt` - Python packages.

Keep all files in the same folder.

## Setup

Python 3.10 or newer.

```
pip install -r requirements.txt
```

## Where the stocks come from

Members are read from CNBC's index pages (www.cnbc.com/nasdaq-100 and /dow-30). If CNBC can't be read, the
dashboard falls back to the last list it saved (`ndx_constituents_cache.json` /
`dow_constituents_cache.json`, created next to the scripts). When the list is not from CNBC, the header says
**"NOT from CNBC"** in red. The "Members from" dropdown can force CNBC only (no saved-list backup).

Nasdaq only: the members are ranked by **QQQ fund weight** to pick the top 30, in this order:

1. Invesco's public QQQ holdings feed (the one behind invesco.com's QQQ page, looked up by CUSIP 46090E103).
2. **Backup:** a holdings file you saved from invesco.com's QQQ page (Portfolio -> **Export data**) into the dashboard
   folder. Any .csv / .xlsx with "holding" or "qqq" in its name; the newest one is used, and the header shows how
   old it is. Re-export it every week or so.
3. The last Invesco weights pulled in this session, if a later refresh fails (marked "latest refresh failed").
4. Last resort: Yahoo market cap. It ranks big foreign ADRs (ASML, ARM) too high, because fund weights count only
   US-listed / free-float shares, so treat the top 30 as approximate when this is used.

Invesco is pulled fresh every time the dashboard starts, on every hourly auto refresh and on **Refresh data**;
nothing is saved between runs. If the new weights change the top 30, prices are reloaded for the new names.
The header says which one was used (and the time of the last successful pull), and the notes at the bottom give the reason if the feed failed. The members
table shows "QQQ wt %" (or "Mkt cap $B" in the last-resort case).

Prices come from Yahoo Finance via yfinance (3 years of dividend-adjusted daily bars).

## Rules (each dashboard mirrors its own QuantConnect algo)

Shared:

1. **Qualify:** 10-day SMA above 200-day SMA, price above 200-day SMA, blended 21/63/126-day momentum positive.
2. **Ranking:** average rank on each momentum return; the top 10 are the picks.
3. **Breadth:** share of the members that qualify. At or below 40% = risk-off, hold nothing.
4. **Index SMA:** a fresh close of QQQ / DIA below its 200-day SMA also goes to cash.
5. **After a trip:** cash until the next Monday rebalance, which resumes if breadth is above 40%.

| | Nasdaq (QQQ top-30 algo) | Dow (Dow 30 algo) |
|---|---|---|
| Universe | Top 30 Nasdaq-100 members by QQQ fund weight (Invesco holdings; Yahoo market cap as backup); GOOG dropped so Alphabet counts once; breadth on these 30 | All 30 members |
| TEO filter | Off | On (Exhausting removed before ranking) |
| Leverage | 2.0 | 2.5 |
| Vol target | 35%: size = 0.35 / (2.0 x basket vol), capped at 100% | Off (always 100%) |
| Per-stock stop | Sold on the first hourly close below its 200-day SMA; cash until Monday | None |
| Checks | Hourly | At the close |

**When the market filters are checked** (the "Market checks" control can switch either dashboard):

- **Nasdaq: hourly**, like the QQQ algo with `filter_check=hourly`. Every completed hourly bar since Monday's
  rebalance is checked: breadth uses each stock's hourly close against its daily SMAs and momentum from the previous
  day, and QQQ's hourly close is checked for a fresh cross below its daily 200-day SMA. Prices shown are the last
  completed hourly close. The page refreshes itself every hour. If hourly prices can't be loaded, it falls back to
  at-the-close checks and says so at the bottom.
- **Dow: at the close.** Everything uses completed daily bars. While the market is open, today's partial bar is
  ignored, so signals only change after 4:00 PM ET.

SMAs, momentum, TEO and basket vol always use completed daily bars.

## What's on the page

- **Cards:** regime (risk-on / risk-off / cash until Monday), since last rebalance (any filter trips this week),
  breadth, index vs its 200-day SMA, basket vol, portfolio size, gross exposure, expected account vol, TEO removed.
- **This week's orders:** type your current holdings (comma separated) and set your account size; it lists BUY,
  HOLD / RESIZE and SELL with target dollars, shares and margin per stock. On the Nasdaq dashboard a holding that hit
  the hourly 200-day stop since Monday shows as SELL with the time it happened.
- **Charts:** 63-day correlation of the picks, breadth over the last year, the index vs its SMA, momentum of the top
  candidates, and the index P/E over the last 3 years (see below).
- **P/E card:** today's trailing-12-month P/E of the stocks traded (Dow 30 / QQQ top 30), its 3-year average, how much of the last 3 years
  was lower (amber at 80%+, green at 20% or less), the P/E a year ago, and Yahoo's own QQQ / DIA P/E as a cross-check (for QQQ that covers all ~100 holdings, so it will differ from
  the top-30 figure).
- **All members table:** sortable and filterable, with qualify, rank, momentum, SMAs, TEO value, slope and stage,
  each stock's own TTM P/E (blank = a loss), and market cap (Nasdaq).
  Picks are starred; stocks removed by TEO are marked "TEO out".
- **Controls:** every algo parameter can be changed at the top of the page. **Refresh data** reloads prices,
  the member list and (Nasdaq) the Invesco weights.

## Index P/E (rolling, trailing 12 months)

No free source has a P/E history for the Dow 30 or the Nasdaq-100 (Yahoo gives QQQ / DIA only as a single
number for today), so the dashboard builds it from the members:

- **EPS:** each member's quarterly reported EPS from Yahoo's earnings calendar (`get_earnings_dates`, about 5 years).
  These are adjusted ("street") EPS, so the P/E reads a little lower than one on GAAP earnings. TTM EPS = the last
  4 quarters, counted from the day the report is out (after-close reports from the next day).
- **Prices:** split-adjusted closes without the dividend adjustment.
- **Basket:** Dow = one share of each member (price-weighted, like the index): P/E = sum of prices / sum of EPS.
  Nasdaq = the top 30 the dashboard trades, at today's QQQ fund weights (Invesco; Yahoo market cap if Invesco
  can't be read), held through time. Losses count as negative earnings.
- **Splits:** if Yahoo has not put older EPS on today's share basis, the dashboard does (checked per split).
- **Left out:** members with no EPS, under 4 quarters of EPS, or a P/E under 4 (usually EPS in a foreign currency
  vs a US-dollar price). They are listed in the notes. A day needs members worth 80% of the basket to get a P/E.
- **Speed:** EPS are pulled once a day and saved in `nasdaq_eps_cache.json` / `dow_eps_cache.json`, so the first
  start of the day takes a little longer. If Yahoo fails for a stock, its last saved
  EPS are used and the notes say so. Prices for the P/E are reloaded at most once an hour.
- It uses today's member list for the whole history, so it is "today's index, back-tested", not the P/E
  published at the time. Expect it to be close to WSJ's / Yahoo's figures, not identical.

## Known differences from the algos

- Nasdaq: the top 30 uses Invesco's latest QQQ weights; the algo re-ranks once a month, so a name near 30th can
  differ mid-month. If Invesco can't be read, the dashboard uses the backups above and says so at the bottom.
- Breadth here divides by all members; the algo divides by members with enough history (they differ only for
  recent IPOs).
- The algo's Monday rebalance uses the 9:45 AM price for "price above the 200-day SMA"; the dashboard uses the last
  completed hourly close (Nasdaq) or the last close (Dow).
- Yahoo's hourly bars run 9:30-10:30, 10:30-11:30 ... 3:30-4:00; QuantConnect's run 9:30-10:00, 10:00-11:00 ...
  So an intraday trip can be timed slightly differently.
- For the QQQ backtest to match the Nasdaq dashboard, run it with `filter_check=hourly` (its default is `close`).
- The breadth history chart uses today's member list for the whole year.
- Market holidays are not skipped when working out "since last rebalance".

## If CNBC fails

CNBC may load its member list with JavaScript, which a plain download can't see. If the header shows the red
"NOT from CNBC" warning, open the CNBC index page in Chrome, press F12, go to Network -> Fetch/XHR, reload, and look
for the request that returns the member list. That request can be wired into `from_cnbc()` in
`momentum_dashboard.py`.
