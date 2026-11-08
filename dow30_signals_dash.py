"""
DOW 30 MOMENTUM + TREND - live signals dashboard (Plotly Dash)

Mirrors the QuantConnect algorithm (dow30_momentum_trend.py, latest version):
  - Trend     : 10-day SMA above the 200-day SMA AND price above the 200-day SMA
  - Momentum  : 21 / 63 / 126-day returns, combined by RANK, blended momentum must be positive
  - Holdings  : top 10 (adjustable on the page), leverage set on the page (each slot = leverage / N of equity)
  - Rebalance : weekly, Monday 15 minutes after the open (9:45 ET; Tuesday if Monday is a holiday)
  - Stop      : an hourly candle closing below the 200-day SMA -> sell that stock
  - Market    : BOTH filter - STOP everything if, on an hourly candle, 40% or fewer of the Dow stocks qualify OR DIA
                crosses below its 200-day SMA (fresh cross). RESUME at the Monday rebalance when more than 40% qualify,
                even if DIA is still below its SMA.
  - Kalshi    : estimated daily carry - funding on positions (risk-free + spread) vs interest on margin
                (7% on margin backing positions at 1.5x+, 3.5% on idle cash; Kalshi promo as of Oct 29, 2026)

Daily SMAs and momentum use COMPLETED daily bars (like QuantConnect's daily consolidator); price is the latest
intraday price. Hourly candles end on the hour (10:00, 11:00 ... 16:00 ET) to match QuantConnect.

Dow 30 tickers: SPDR DIA daily holdings file -> Wikipedia -> built-in list (in that order).

Run:   pip install -r requirements.txt
       python dow30_signals_dash.py      then open http://127.0.0.1:8050
"""
import io
import threading
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import yfinance as yf
from dash import Dash, Input, Output, State, dash_table, dcc, html

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    from backports.zoneinfo import ZoneInfo

# ───────── settings (match the QuantConnect parameters) ─────────
FAST, SLOW = 10, 200
MOM_LENS = (21, 63, 126)
MOM_METHOD = "rank"        # "rank" or "return"
MOM_POSITIVE = "blend"     # "blend", "all" or "off"
TOP_N = 10                 # default; adjustable on the page
DEFAULT_LEVERAGE = 2.5     # adjustable on the page (matches the algorithm)
MARKET_ETF, MARKET_SMA = "DIA", 200
MARKET_FILTER = "both"     # "both", "breadth", "sma" or "off"
BREADTH_MIN = 0.40         # trade only when MORE than this share of the stocks qualify
REBAL_WEEKDAY = 0           # 0 = Monday (week start)
REBAL_MINUTES_AFTER_OPEN = 15   # set 31 to match after_market_open(spy, 31)
DATA_REFRESH_SEC = 300     # re-download prices every 5 minutes
# Kalshi carry assumptions (edit to current values)
RISK_FREE = 0.04           # starting value for the funding box on the page (% per year on the full position value)
FUNDING_SPREAD = 0.0       # Kalshi's real funding is market-driven - enter the rate from the market page
PROMO_POS_APY, PROMO_CASH_APY, PROMO_MIN_LEV = 0.07, 0.035, 1.5
KALSHI_FEE = 0.0002        # per side, of the full position value
ET = ZoneInfo("America/New_York")

FALLBACK_DOW30 = ["AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
                  "GOOGL", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM",
                  "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "WMT"]
HEADERS = {"User-Agent": "Mozilla/5.0 (dow30-signals dashboard)"}


# ───────── Dow 30 tickers ─────────
def _clean(tickers):
    out = []
    for t in tickers:
        t = str(t).strip().upper().replace(".", "-")
        if t and t.replace("-", "").isalpha() and len(t) <= 6 and t not in out:
            out.append(t)
    return out


def tickers_from_spdr():
    url = ("https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/"
           "holdings-daily-us-en-dia.xlsx")
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    raw = pd.read_excel(io.BytesIO(r.content), header=None)
    hdr = next(i for i in range(min(len(raw), 15)) if "Ticker" in raw.iloc[i].astype(str).tolist())
    df = pd.read_excel(io.BytesIO(r.content), header=hdr)
    t = _clean(df["Ticker"].dropna().tolist())
    t = [x for x in t if x not in ("CASH", "USD", "-")]
    if not 28 <= len(t) <= 31:
        raise ValueError(f"SPDR file gave {len(t)} tickers")
    return t


def tickers_from_wikipedia():
    r = requests.get("https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average", headers=HEADERS, timeout=20)
    r.raise_for_status()
    for tbl in pd.read_html(io.StringIO(r.text)):
        col = next((c for c in tbl.columns if str(c).strip().lower() in ("symbol", "ticker")), None)
        if col is not None and 28 <= len(tbl) <= 31:
            return _clean(tbl[col].tolist())
    raise ValueError("no Dow table found")


def get_dow30():
    for name, fn in (("SPDR DIA holdings", tickers_from_spdr), ("Wikipedia", tickers_from_wikipedia)):
        try:
            t = fn()
            return t, name
        except Exception as e:  # noqa: BLE001
            print(f"[tickers] {name} failed: {e}")
    return list(FALLBACK_DOW30), "built-in list (online sources failed)"


# ───────── prices ─────────
def _closes(df):
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = df.columns.get_level_values(0)
        return df["Close"] if "Close" in lvl0 else df.xs("Close", axis=1, level=1)
    return df[["Close"]]


def download_daily(tickers):
    df = yf.download(tickers, period="2y", interval="1d", auto_adjust=True, progress=False, threads=True)
    c = _closes(df)
    c.index = pd.to_datetime(c.index).tz_localize(None).normalize()
    return c


def download_intraday(tickers):
    df = yf.download(tickers, period="5d", interval="30m", auto_adjust=True, prepost=False,
                     progress=False, threads=True)
    c = _closes(df)
    idx = pd.to_datetime(c.index)
    c.index = idx.tz_convert(ET) if idx.tz is not None else idx.tz_localize("UTC").tz_convert(ET)
    return c


def hourly_bars(c30, now):
    """30-minute bars (labelled by start) -> completed candles ending on the hour, like QuantConnect."""
    if c30.empty:
        return c30
    end = c30.index + pd.Timedelta(minutes=30)
    bucket = end.ceil("h")
    h = c30.groupby(bucket).last()
    return h[h.index <= now]


# ───────── market clock ─────────
def market_open(now):
    return now.weekday() < 5 and (now.hour, now.minute) >= (9, 30) and now.hour < 16


def next_rebalance(now):
    d = now.date()
    days_ahead = (REBAL_WEEKDAY - now.weekday()) % 7
    target = datetime.combine(d + timedelta(days=days_ahead), datetime.min.time(), ET).replace(
        hour=9, minute=30) + timedelta(minutes=REBAL_MINUTES_AFTER_OPEN)
    if target < now:
        target += timedelta(days=7)
    return target


# ───────── signals ─────────
def last_rebalance(now):
    nr = next_rebalance(now)
    return nr - timedelta(days=7)


def compute(daily, intra30, tickers, now, leverage, top_n=TOP_N):
    """Signal tables + market-filter state. daily = daily closes; intra30 = 30-minute closes (ET)."""
    today = pd.Timestamp(now.date())
    if (market_open(now) or now.hour < 9 or (now.hour == 9 and now.minute < 30)) and today in daily.index:
        daily = daily[daily.index < today]                      # COMPLETED daily bars only
    hourly = hourly_bars(intra30, now)

    def last_price(t):
        if t in intra30.columns and intra30[t].dropna().size:
            return float(intra30[t].dropna().iloc[-1])
        return float(daily[t].dropna().iloc[-1]) if t in daily.columns and daily[t].dropna().size else np.nan

    def last_hour(t):
        if t in hourly.columns and hourly[t].dropna().size:
            sr = hourly[t].dropna()
            return float(sr.iloc[-1]), sr.index[-1]
        return np.nan, None

    since = last_rebalance(now)
    rows, levels = [], {}
    for t in tickers:
        if t not in daily.columns:
            rows.append(dict(Ticker=t, Status="no data"))
            continue
        sr = daily[t].dropna()
        if len(sr) < max(SLOW, max(MOM_LENS) + 1):
            rows.append(dict(Ticker=t, Status=f"only {len(sr)} days of history"))
            continue
        fast, slow = sr.iloc[-FAST:].mean(), sr.iloc[-SLOW:].mean()
        moms = [sr.iloc[-1] / sr.iloc[-1 - n] - 1 for n in MOM_LENS]
        avg = float(np.mean(moms))
        mom_ok = not ((MOM_POSITIVE == "blend" and avg <= 0) or (MOM_POSITIVE == "all" and min(moms) <= 0))
        levels[t] = (fast, slow, mom_ok)
        px = last_price(t)
        hc, ht = last_hour(t)
        # first hourly close below the slow SMA since the last Monday rebalance (the algo sells there and stays out)
        stop_time, stop_px = None, np.nan
        if t in hourly.columns:
            hs = hourly[t].dropna()
            hs = hs[hs.index > since]
            below = hs[hs < slow]
            if len(below):
                stop_time, stop_px = below.index[0], float(below.iloc[0])
        reasons = []
        if not fast > slow:
            reasons.append(f"{FAST} SMA below {SLOW} SMA")
        if not px > slow:
            reasons.append(f"price below {SLOW} SMA")
        if MOM_POSITIVE == "blend" and avg <= 0:
            reasons.append("momentum negative")
        if MOM_POSITIVE == "all" and min(moms) <= 0:
            reasons.append("a lookback is negative")
        rows.append(dict(Ticker=t, Price=px, **{f"SMA{FAST}": fast, f"SMA{SLOW}": slow},
                         AboveSlowPct=(px / slow - 1) * 100,
                         **{f"Mom{n}": m * 100 for n, m in zip(MOM_LENS, moms)},
                         MomAvg=avg * 100, HourClose=hc, HourTime=ht.strftime("%a %H:%M") if ht is not None else "",
                         StopHit=bool(hc < slow) if not np.isnan(hc) else False,
                         StoppedWeek=stop_time is not None,
                         StopTime=stop_time.strftime("%a %H:%M") if stop_time is not None else "", StopPx=stop_px,
                         Qualifies=not reasons, Status="qualifies" if not reasons else "; ".join(reasons)))
    uni = pd.DataFrame(rows)

    # rank the qualifiers
    q = uni[uni.get("Qualifies", False) == True].copy()  # noqa: E712
    if len(q):
        if MOM_METHOD == "rank":
            q["Score"] = 0.0
            for n in MOM_LENS:
                q["Score"] -= q[f"Mom{n}"].rank(ascending=False, method="first") / len(MOM_LENS)
        else:
            q["Score"] = q["MomAvg"]
        q = q.sort_values("Score", ascending=False)
        q["Rank"] = range(1, len(q) + 1)
    top = q.head(top_n).copy()
    top["Weight"] = leverage / top_n

    # ── market filter ──
    m = daily[MARKET_ETF].dropna() if MARKET_ETF in daily.columns else pd.Series(dtype=float)
    mkt_sma = m.iloc[-MARKET_SMA:].mean() if len(m) >= MARKET_SMA else np.nan
    mkt_px = last_price(MARKET_ETF)
    n_ok = len(levels)
    n_q = int((uni["Qualifies"] == True).sum()) if "Qualifies" in uni.columns else 0  # noqa: E712
    share = n_q / n_ok if n_ok else 0.0

    # replay every hourly candle since the last Monday rebalance: did breadth or a DIA cross trip the filter?
    trips, hour_rows = [], []
    if n_ok and not hourly.empty:
        hidx = hourly.index[hourly.index > since]
        dia_h = hourly[MARKET_ETF].dropna() if MARKET_ETF in hourly.columns else pd.Series(dtype=float)
        prev_above = None
        before = dia_h[dia_h.index <= since]
        if len(before) and not np.isnan(mkt_sma):
            prev_above = bool(before.iloc[-1] >= mkt_sma)
        for h in hidx:
            nq = 0
            for t, (fast, slow, mom_ok) in levels.items():
                c = hourly.at[h, t] if t in hourly.columns else np.nan
                nq += bool(fast > slow and not np.isnan(c) and c > slow and mom_ok)
            sh = nq / n_ok
            hour_rows.append((h, nq, sh))
            if MARKET_FILTER in ("breadth", "both") and sh <= BREADTH_MIN:
                trips.append((h, f"breadth {nq} of {n_ok} ({sh:.0%})"))
            if h in dia_h.index and not np.isnan(mkt_sma):
                above = bool(dia_h.at[h] >= mkt_sma)
                if MARKET_FILTER == "both" and prev_above is True and not above:
                    trips.append((h, f"{MARKET_ETF} crossed below its {MARKET_SMA} SMA"))
                if MARKET_FILTER == "sma" and not above:
                    trips.append((h, f"{MARKET_ETF} below its {MARKET_SMA} SMA"))
                prev_above = above
    last_h = hour_rows[-1] if hour_rows else (None, n_q, share)

    if MARKET_FILTER in ("breadth", "both"):
        resume_ok = n_ok > 0 and share > BREADTH_MIN            # what Monday's rebalance will decide
    elif MARKET_FILTER == "sma":
        resume_ok = not (mkt_px < mkt_sma)
    else:
        resume_ok = True
    market = dict(price=mkt_px, sma=mkt_sma, n=n_ok, n_q=n_q, share=share,
                  n_qh=last_h[1], share_h=last_h[2], hour_time=last_h[0].strftime("%a %H:%M") if last_h[0] is not None else "",
                  trips=trips, stopped=bool(trips), resume_ok=resume_ok, since=since,
                  below_sma=bool(mkt_px < mkt_sma) if not np.isnan(mkt_sma) else False)
    if not resume_ok:
        top = top.iloc[0:0]
    return uni, q, top, market


def carry(equity, leverage, n_positions, funding_rate=None, top_n=TOP_N):
    """Estimated Kalshi yearly carry for the target portfolio. funding_rate = annual rate on the position value."""
    if funding_rate is None:
        funding_rate = RISK_FREE + FUNDING_SPREAD
    notional = equity * leverage * n_positions / top_n
    funding = notional * funding_rate
    backing = min(notional / max(leverage, 1e-9), equity)
    pos_apy = PROMO_POS_APY if leverage >= PROMO_MIN_LEV else PROMO_CASH_APY
    interest = backing * pos_apy + max(equity - backing, 0) * PROMO_CASH_APY
    return dict(notional=notional, funding=funding, interest=interest, net=interest - funding, pos_apy=pos_apy,
                backing=backing, idle=max(equity - backing, 0), funding_rate=funding_rate,
                fee_round_trip=notional * KALSHI_FEE * 2)


# ───────── cache (downloads run at most every DATA_REFRESH_SEC) ─────────
_cache = {"t": 0.0, "tickers": None, "source": "", "daily": None, "intra": None, "tick_day": None, "error": ""}
_lock = threading.Lock()


def get_data(force=False):
    with _lock:
        now = time.time()
        day = datetime.now(ET).date()
        if _cache["tickers"] is None or _cache["tick_day"] != day:
            _cache["tickers"], _cache["source"] = get_dow30()
            _cache["tick_day"] = day
            force = True
        if force or now - _cache["t"] > DATA_REFRESH_SEC or _cache["daily"] is None:
            syms = _cache["tickers"] + [MARKET_ETF]
            try:
                _cache["daily"] = download_daily(syms)
                _cache["intra"] = download_intraday(syms)
                _cache["t"] = now
                _cache["error"] = ""
            except Exception as e:  # noqa: BLE001
                _cache["error"] = f"Price download failed: {e}"
        return dict(_cache)


# ───────── app ─────────
BG, CARD, TXT, MUTED = "#0f1115", "#181b22", "#e6e6e6", "#9aa0aa"
GREEN, RED, AMBER = "#2ecc71", "#e74c3c", "#f5a623"
card_style = {"background": CARD, "padding": "14px 16px", "borderRadius": "10px", "flex": "1", "minWidth": "200px"}
table_style = dict(
    style_table={"overflowX": "auto"},
    style_header={"backgroundColor": "#232733", "color": TXT, "fontWeight": "600", "border": "none"},
    style_cell={"backgroundColor": CARD, "color": TXT, "border": "none", "padding": "6px 10px",
                "fontFamily": "Inter, Segoe UI, sans-serif", "fontSize": "13px", "textAlign": "right"},
    style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in ("Ticker", "Status", "Action", "Why")],
)

app = Dash(__name__, title="Dow 30 Signals")
app.layout = html.Div(style={"background": BG, "color": TXT, "minHeight": "100vh", "padding": "18px",
                             "fontFamily": "Inter, Segoe UI, sans-serif"}, children=[
    html.H2("Dow 30 momentum + trend - live signals", style={"margin": "0 0 4px"}),
    html.Div(id="subtitle", style={"color": MUTED, "marginBottom": "14px"}),
    html.Div(style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "marginBottom": "14px"}, children=[
        html.Div(style=card_style, children=[html.Div("Account equity ($)", style={"color": MUTED}),
                 dcc.Input(id="equity", type="number", value=10000, debounce=True, persistence=True,
                           style={"width": "100%", "marginTop": "6px", "color": "#111"})]),
        html.Div(style=card_style, children=[html.Div("Leverage (x, total across all holdings)", style={"color": MUTED}),
                 dcc.Input(id="leverage", type="number", value=DEFAULT_LEVERAGE, min=0.25, max=10, step=0.25,
                           debounce=True, persistence=True, style={"width": "100%", "marginTop": "6px", "color": "#111"})]),
        html.Div(style=card_style, children=[html.Div("Number of stocks to hold (top N)", style={"color": MUTED}),
                 dcc.Input(id="topn", type="number", value=TOP_N, min=1, max=30, step=1,
                           debounce=True, persistence=True, style={"width": "100%", "marginTop": "6px", "color": "#111"})]),
        html.Div(style=card_style, children=[html.Div("Kalshi funding (% per year, + = longs pay)", style={"color": MUTED}),
                 dcc.Input(id="funding", type="number", value=round((RISK_FREE + FUNDING_SPREAD) * 100, 2), step=0.25,
                           debounce=True, persistence=True, style={"width": "100%", "marginTop": "6px", "color": "#111"})]),
        html.Div(style={**card_style, "flex": "3"}, children=[
            html.Div("Current holdings (tickers, comma-separated)", style={"color": MUTED}),
            dcc.Input(id="holdings", type="text", value="", debounce=True, persistence=True,
                      placeholder="e.g. NVDA, GS, CAT", style={"width": "100%", "marginTop": "6px", "color": "#111"})]),
        html.Div(style=card_style, children=[html.Button("Refresh prices now", id="refresh", n_clicks=0,
                                                         style={"width": "100%", "height": "100%"})]),
    ]),
    html.Div(id="cards", style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "marginBottom": "14px"}),
    html.H3("Alerts"), html.Div(id="alerts"),
    html.H3("Orders at the next rebalance"), html.Div(id="orders"),
    html.H3(id="targets-title"), html.Div(id="targets"),
    html.H3("Chart"),
    dcc.Dropdown(id="chart-ticker", value=MARKET_ETF, clearable=False, style={"color": "#111", "maxWidth": "260px"},
                 options=[{"label": t, "value": t} for t in [MARKET_ETF] + sorted(FALLBACK_DOW30)]),
    dcc.Graph(id="chart", style={"height": "460px"}),
    html.H3("All Dow 30 stocks"), html.Div(id="universe"),
    dcc.Interval(id="tick", interval=60_000, n_intervals=0),
])


def fmt_table(df, cols, money=(), pct=(), num=(), id_=None, cond=None):
    data = df[cols].copy()
    for c in money:
        data[c] = data[c].map(lambda x: f"{x:,.2f}" if pd.notna(x) else "")
    for c in pct:
        data[c] = data[c].map(lambda x: f"{x:+.1f}%" if pd.notna(x) else "")
    for c in num:
        data[c] = data[c].map(lambda x: f"{x:,.0f}" if pd.notna(x) else "")
    kw = {"id": id_} if id_ else {}
    return dash_table.DataTable(data=data.to_dict("records"), columns=[{"name": c, "id": c} for c in cols],
                                style_data_conditional=cond or [], sort_action="native", **kw, **table_style)


def card(title, value, sub="", color=TXT):
    return html.Div(style=card_style, children=[
        html.Div(title, style={"color": MUTED, "fontSize": "13px"}),
        html.Div(value, style={"fontSize": "22px", "fontWeight": "700", "color": color, "margin": "4px 0"}),
        html.Div(sub, style={"color": MUTED, "fontSize": "12px"})])


@app.callback(
    Output("subtitle", "children"), Output("cards", "children"), Output("alerts", "children"),
    Output("orders", "children"), Output("targets-title", "children"), Output("targets", "children"),
    Output("universe", "children"), Output("chart-ticker", "options"),
    Input("tick", "n_intervals"), Input("refresh", "n_clicks"), Input("equity", "value"),
    Input("leverage", "value"), Input("holdings", "value"), Input("funding", "value"), Input("topn", "value"))
def update(_n, clicks, equity, leverage, holdings, funding=None, topn=None):
    from dash import ctx
    try:
        trig = ctx.triggered_id
    except Exception:  # noqa: BLE001  (called outside a callback)
        trig = None
    d = get_data(force=trig == "refresh")
    now = datetime.now(ET)
    if d["daily"] is None:
        msg = html.Div(d["error"] or "Loading...", style={"color": RED})
        return msg, [], msg, "", "", "", "", []
    lev = float(leverage) if leverage else DEFAULT_LEVERAGE
    equity = float(equity or 0)
    held = _clean((holdings or "").replace(" ", ",").split(","))
    top_n = int(topn) if topn else TOP_N
    uni, ranked, top, mk = compute(d["daily"], d["intra"], d["tickers"], now, lev, top_n)
    slot = lev / top_n

    upd = datetime.fromtimestamp(d["t"], ET).strftime("%a %b %d %H:%M ET") if d["t"] else "-"
    subtitle = (f"Tickers: {d['source']} ({len(d['tickers'])}) · prices updated {upd} · "
                f"{'MARKET OPEN' if market_open(now) else 'market closed'} · "
                f"{FAST}/{SLOW} SMA · momentum {'/'.join(map(str, MOM_LENS))} by {MOM_METHOD} · top {top_n} at {lev:g}x "
                f"({slot:.1%} each) · market filter: {MARKET_FILTER}")
    if d["error"]:
        subtitle += f" · {d['error']}"

    # ── market filter card ──
    need = int(np.floor(BREADTH_MIN * mk["n"])) + 1
    gap = (mk["price"] / mk["sma"] - 1) * 100 if mk["sma"] else np.nan
    if mk["stopped"]:
        first = mk["trips"][0]
        mtext, mcolor = "STOPPED - out until Monday", RED
        msub = f"tripped {first[0]:%a %H:%M}: {first[1]} · "
    elif not mk["resume_ok"]:
        mtext, mcolor, msub = "RISK OFF - no longs", RED, ""
    else:
        mtext, mcolor, msub = "ON - longs allowed", GREEN, ""
    msub += (f"breadth {mk['n_q']} of {mk['n']} ({mk['share']:.0%}), need {need}+ · "
             f"{MARKET_ETF} {mk['price']:,.2f} vs {MARKET_SMA} SMA {mk['sma']:,.2f} ({gap:+.1f}%)"
             + (" - below, breadth decides" if mk["below_sma"] and MARKET_FILTER == "both" else ""))
    friday = "Monday: RESUME/BUY" if mk["resume_ok"] else "Monday: stay in cash"

    nr = next_rebalance(now)
    left = nr - now
    countdown = f"{left.days}d {left.seconds // 3600}h {(left.seconds % 3600) // 60}m"
    f_rate = (float(funding) / 100) if funding is not None else None
    c = carry(equity, lev, len(top), f_rate, top_n)
    cards = [
        card("Market filter (breadth OR DIA cross)", mtext, msub, mcolor),
        card("Next rebalance", nr.strftime("%a %b %d, %H:%M ET"), f"in {countdown} · {friday}",
             GREEN if mk["resume_ok"] else AMBER),
        card("Qualifying stocks", f"{len(ranked)} of {len(d['tickers'])}",
             f"{len(top)} in the target · {top_n - len(top)} slot(s) in cash"),
        card("Target exposure", f"{len(top) * slot:.2f}x", f"${c['notional']:,.0f} of positions"),
        card("Kalshi carry (est. per year)", f"${c['net']:+,.0f}",
             f"interest +${c['interest']:,.0f} = {c['pos_apy']:.1%} on ${c['backing']:,.0f} of margin backing positions"
             f" + {PROMO_CASH_APY:.1%} on ${c['idle']:,.0f} idle · funding {'-' if c['funding'] >= 0 else '+'}"
             f"${abs(c['funding']):,.0f} = {c['funding_rate']:.2%} on ${c['notional']:,.0f} of positions (your input - "
             f"use Kalshi's current rate) · fees ~${c['fee_round_trip']:,.0f} per full round trip",
             GREEN if c["net"] >= 0 else AMBER),
    ]

    # ── alerts ──
    alerts = []
    if mk["stopped"]:
        txt = "; ".join(f"{h:%a %H:%M} {why}" for h, why in mk["trips"][:3])
        alerts.append(html.Div(f"MARKET FILTER TRIPPED since the last rebalance ({txt}) -> SELL EVERYTHING and stay "
                               f"in cash until Monday's rebalance.", style={"color": RED, "fontWeight": "700"}))
    u_idx = uni.set_index("Ticker")
    for t in held:
        if t not in u_idx.index or "Price" not in u_idx.columns or pd.isna(u_idx.loc[t].get("Price", np.nan)):
            alerts.append(html.Div(f"{t}: not in the current Dow 30 or no data -> sell at the rebalance.", style={"color": AMBER}))
            continue
        r = u_idx.loc[t]
        if r.get("StoppedWeek", False):
            now_txt = ("still below" if r["StopHit"] else
                       f"back above now ({r['HourClose']:,.2f}) - stay out anyway")
            alerts.append(html.Div(f"{t}: STOPPED {r['StopTime']} - hourly close {r['StopPx']:,.2f} below its {SLOW} SMA "
                                   f"{r[f'SMA{SLOW}']:,.2f} -> SELL / stay out until Monday's rebalance ({now_txt}).",
                                   style={"color": RED, "fontWeight": "700"}))
        else:
            alerts.append(html.Div(f"{t}: OK - {r['AboveSlowPct']:+.1f}% above its {SLOW} SMA "
                                   f"(last hourly close {r['HourClose']:,.2f}).", style={"color": GREEN}))
    if not held and not mk["stopped"]:
        alerts = [html.Div("No alerts. Enter your current holdings above to get stop alerts.", style={"color": MUTED})]

    # ── orders at the next rebalance ──
    tgt = set(top["Ticker"]) if len(top) else set()
    orders = []
    for t in sorted(tgt | set(held)):
        stopped = t in u_idx.index and bool(u_idx.loc[t].get("StoppedWeek", False))
        if t in tgt and t in held and stopped:
            act, why = "BUY (re-enter)", f"stopped {u_idx.loc[t, 'StopTime']}, qualifies again - rank {int(top.set_index('Ticker').loc[t, 'Rank'])}"
        elif t in tgt and t in held:
            act, why = "HOLD / resize", f"still in the top {top_n}"
        elif t in tgt:
            act, why = "BUY", f"rank {int(top.set_index('Ticker').loc[t, 'Rank'])}"
        else:
            row = u_idx.loc[t] if t in u_idx.index else None
            why = (f"market filter: breadth {mk['share']:.0%} (need more than {BREADTH_MIN:.0%})" if not mk["resume_ok"] else
                   row["Status"] if row is not None and row.get("Status") != "qualifies" else f"dropped out of the top {top_n}")
            act = "SELL"
        price = u_idx.loc[t, "Price"] if t in u_idx.index and "Price" in u_idx.columns else np.nan
        dollars = equity * slot if act != "SELL" else 0.0
        orders.append(dict(Ticker=t, Action=act, Why=why, Price=price, TargetDollars=dollars,
                           TargetShares=dollars / price if price and price > 0 else np.nan))
    od = pd.DataFrame(orders)
    orders_tbl = fmt_table(od, ["Ticker", "Action", "Why", "Price", "TargetDollars", "TargetShares"],
                           money=("Price", "TargetDollars"), num=("TargetShares",),
                           cond=[{"if": {"filter_query": '{Action} contains "BUY"'}, "color": GREEN},
                                 {"if": {"filter_query": '{Action} = "SELL"'}, "color": RED}]) if len(od) else \
        html.Div("No positions and nothing to buy.", style={"color": MUTED})

    # ── target portfolio ──
    t_title = f"Target portfolio (top {top_n}, {lev:g}x -> {slot:.1%} each)"
    if len(top):
        tp = top.copy()
        tp["Dollars"] = equity * tp["Weight"]
        tp["Shares"] = tp["Dollars"] / tp["Price"]
        tp["Weight %"] = (tp["Weight"] * 100).round(1)
        mcols = [f"Mom{n}" for n in MOM_LENS]
        targets = fmt_table(tp, ["Rank", "Ticker", "Price", f"SMA{SLOW}", "AboveSlowPct", *mcols, "Weight %", "Dollars", "Shares"],
                            money=("Price", f"SMA{SLOW}", "Dollars"), pct=("AboveSlowPct", *mcols), num=("Shares",))
    else:
        targets = html.Div("Market filter is risk off - the target is 100% cash." if not mk["resume_ok"]
                           else "No stocks qualify - 100% cash.", style={"color": AMBER})

    # ── universe ──
    full = uni.copy()
    for col in ["Price", f"SMA{FAST}", f"SMA{SLOW}", "AboveSlowPct", "MomAvg", *[f"Mom{n}" for n in MOM_LENS]]:
        if col not in full.columns:
            full[col] = np.nan
    if len(ranked):
        full = full.merge(ranked[["Ticker", "Rank"]], on="Ticker", how="left")
    else:
        full["Rank"] = np.nan
    full["_r"] = pd.to_numeric(full["Rank"], errors="coerce").fillna(999)
    full = full.sort_values(["_r", "Ticker"])
    full["Rank"] = full["Rank"].map(lambda x: "" if pd.isna(x) else int(x))
    universe = fmt_table(full, ["Rank", "Ticker", "Status", "Price", f"SMA{FAST}", f"SMA{SLOW}", "AboveSlowPct",
                                *[f"Mom{n}" for n in MOM_LENS], "MomAvg"],
                         money=("Price", f"SMA{FAST}", f"SMA{SLOW}"),
                         pct=("AboveSlowPct", "MomAvg", *[f"Mom{n}" for n in MOM_LENS]),
                         cond=[{"if": {"filter_query": '{Status} = "qualifies"', "column_id": "Status"}, "color": GREEN}])
    opts = [{"label": t, "value": t} for t in [MARKET_ETF] + sorted(d["tickers"])]
    return subtitle, cards, alerts, orders_tbl, t_title, targets, universe, opts


@app.callback(Output("chart", "figure"), Input("chart-ticker", "value"), Input("tick", "n_intervals"))
def chart(ticker, _n):
    ticker = ticker or MARKET_ETF
    d = get_data()
    fig = go.Figure()
    fig.update_layout(template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=CARD, margin=dict(l=40, r=20, t=70, b=30),
                      legend=dict(orientation="h", y=1.02, yanchor="bottom", x=1, xanchor="right"),
                      title=dict(text=ticker, x=0.01, y=0.97))
    if d["daily"] is None or ticker not in d["daily"].columns:
        return fig
    s = d["daily"][ticker].dropna()
    fig.add_trace(go.Scatter(x=s.index, y=s, name="Close", line=dict(color="#d0d4dc", width=1.5)))
    fig.add_trace(go.Scatter(x=s.index, y=s.rolling(FAST).mean(), name=f"{FAST} SMA", line=dict(color="#4aa3ff", width=1.2)))
    slow_len = MARKET_SMA if ticker == MARKET_ETF else SLOW
    fig.add_trace(go.Scatter(x=s.index, y=s.rolling(slow_len).mean(), name=f"{slow_len} SMA", line=dict(color=AMBER, width=2)))
    fig.update_xaxes(range=[s.index[-1] - pd.Timedelta(days=365), s.index[-1] + pd.Timedelta(days=3)])
    return fig


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=8050)
