# Momentum + Trend live signals dashboards (Nasdaq-100 and Dow 30)

Two Plotly Dash dashboards that apply the QuantConnect momentum + trend algos to today's market, so you can see
this week's picks, the market filters and the orders to place.

| Dashboard | Run | Opens at | Market filter | Vol target |
|---|---|---|---|---|
| Nasdaq-100 | `python nasdaq_dashboard.py` (or `run_nasdaq.bat`) | http://127.0.0.1:8050 | QQQ | 35% |
| Dow 30 | `python dow_dashboard.py` (or `run_dow.bat`) | http://127.0.0.1:8051 | DIA | off |

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
dashboard falls back to Wikipedia's member table, then to the last list it saved (`ndx_constituents_cache.json` /
`dow_constituents_cache.json`, created next to the scripts). When the list is not from CNBC, the header says
**"NOT from CNBC"** in red. The "Members from" dropdown can force CNBC only or Wikipedia.

Prices come from Yahoo Finance via yfinance (3 years of dividend-adjusted daily bars).

## Rules (same as the latest QQQ algo; Dow identical except DIA and no vol target)

1. **Qualify:** 10-day SMA above 200-day SMA, price above 200-day SMA, blended 21/63/126-day momentum positive.
2. **TEO filter:** qualifying stocks whose Trend Efficiency Oscillator stage is **Exhausting** are removed before
   ranking. TEO = signed Kaufman efficiency ratio (21 days), EMA-smoothed (7); stage from the 6-day slope with fixed
   zones 0.50 / 0.25 / 0.10 and flat threshold 0.02.
3. **Ranking:** average rank on 21, 63 and 126-day returns; the top 10 are the picks.
4. **Breadth:** share of ALL members that qualify (rule 1). At or below 40% = risk-off, hold nothing.
5. **Index SMA:** a fresh close of QQQ / DIA below its 200-day SMA also goes to cash.
6. **When filters are checked:** breadth and the index SMA are checked at every daily close. A trip means cash until
   the next Monday rebalance, which resumes if breadth is above 40%.
7. **Sizing:** leverage 2.5, each pick gets size x 2.5 / 10 of the account. Nasdaq: size = 35% / (2.5 x basket vol),
   capped at 100% (basket vol = higher of the 21 and 63-day realized vol of the 10 picks). Dow: always 100%.
8. **No per-stock stop:** holdings change only at the Monday rebalance or when a market filter trips.

**All checks are at the close.** Everything uses completed daily bars. While the market is open, today's partial bar
is ignored, so signals only change after 4:00 PM ET.

## What's on the page

- **Cards:** regime (risk-on / risk-off / cash until Monday), since last rebalance (any filter trips this week),
  breadth, index vs its 200-day SMA, basket vol, portfolio size, gross exposure, expected account vol, TEO removed.
- **This week's orders:** type your current holdings (comma separated) and set your account size; it lists BUY,
  HOLD / RESIZE and SELL with target dollars, shares and margin per stock.
- **Charts:** 63-day correlation of the picks, breadth over the last year, the index vs its SMA, momentum of the top
  candidates.
- **All members table:** sortable and filterable, with qualify, rank, momentum, SMAs, TEO value, slope and stage.
  Picks are starred; stocks removed by TEO are marked "TEO out".
- **Controls:** every algo parameter can be changed at the top of the page. **Refresh data** reloads prices and
  the member list.

## Known differences from the algos

- Breadth here divides by all members; the algo divides by members with enough history (they differ only for
  recent IPOs).
- The algo's Monday rebalance uses the 9:45 AM price for "price above the 200-day SMA"; the dashboard uses the last
  close.
- The breadth history chart uses today's member list for the whole year.
- Market holidays are not skipped when working out "since last rebalance".

## If CNBC fails

CNBC may load its member list with JavaScript, which a plain download can't see. If the header shows the red
"NOT from CNBC" warning, open the CNBC index page in Chrome, press F12, go to Network -> Fetch/XHR, reload, and look
for the request that returns the member list. That request can be wired into `from_cnbc()` in
`momentum_dashboard.py`.
