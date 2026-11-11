"""
MOMENTUM + TREND - live signals dashboard (Plotly Dash + yfinance) for the NASDAQ-100 or the DOW 30.

One engine, two dashboards:
    python nasdaq_dashboard.py    ->  http://127.0.0.1:8050   (Nasdaq-100, market filter = QQQ)
    python dow_dashboard.py       ->  http://127.0.0.1:8051   (Dow 30,     market filter = DIA)
    (or: python momentum_dashboard.py nasdaq | dow)

Mirrors the latest QuantConnect algo (the QQQ version):
  * Trend      : a stock qualifies while its 10-day SMA > 200-day SMA AND price > 200-day SMA,
                 and blended momentum is positive (mom_positive = blend | all | off).
  * Momentum   : 21 / 63 / 126-day returns, ranked (average rank) or averaged (return).
  * TEO filter : qualifying stocks whose Trend Efficiency Oscillator stage is EXHAUSTING are removed BEFORE ranking;
                 the top 10 come from the rest. TEO = signed Kaufman efficiency ratio (21d), EMA-smoothed (7),
                 stage from the 6-day direction-aware slope. Breadth is not affected.
  * Breadth    : risk-off if breadth_min (Nasdaq 30%, Dow 40%) OR FEWER of the members qualify -> hold nothing.
  * Momentum   : Nasdaq 21 + 126 days, Dow 21 + 63 + 126 days.
  * Index SMA  : the algo also goes to cash on a FRESH cross of the market ETF (QQQ / DIA) below its 200-day SMA,
                 judged at the daily close. Both filters are checked at every close; once out, the algo stays in cash
                 until the next Monday rebalance, and resumes there if breadth is above breadth_min.
  * Picks      : top 10, each gets  size x leverage / 10  of the account (leverage 2.5).
  * Vol target : Nasdaq only (Dow: off, always 100% deployed).
                 size (share of the account deployed, 0-100%) = vol_target (35%) / (leverage x basket vol);
                 basket vol = realized vol of the top-10 basket (weights 1/10) over 21 and 63 days, the higher one.
  * Stop       : none per stock - holdings change only at the rebalance or when a market filter trips.

WHEN THE MARKET FILTERS ARE CHECKED ("Market checks" control; default per index):
  * Nasdaq: HOURLY - like the algo with filter_check=hourly. Every completed hourly bar since the last rebalance is
    checked: breadth uses each stock's hourly close vs its daily SMAs / momentum as of the previous day, and QQQ's
    hourly close is checked for a fresh cross below its daily 200-day SMA. "Price" is the last completed hourly close.
    The page refreshes itself every hour (prices only; the Refresh button also reloads the member list).
  * Dow: AT THE CLOSE - everything uses completed daily bars; while the market is open, today's partial bar is
    ignored, so the signals only change after 4:00 PM ET.
  SMAs, momentum, TEO and basket vol always use completed daily bars.

Members come from CNBC's index page (www.cnbc.com/nasdaq-100 or /dow-30). If CNBC can't be read, the dashboard falls
back to Wikipedia's member table, then to the last list it saved (a json file next to this script), and says so in
red at the top.

    pip install dash plotly pandas numpy yfinance requests lxml
"""
import datetime as dt
import io
import json
import math
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import yfinance as yf
from dash import Dash, Input, Output, State, dash_table, dcc, html

# ───────────────────────── settings ─────────────────────────
DEFAULTS = dict(
    fast=10, slow=200, momentum="21,63,126", mom_method="rank", mom_positive="blend",
    top=10, leverage=2.5, breadth_min=0.40, market_sma=200,
    vol_target=0.35, vol_lookbacks="21,63", vol_min_size=0.0,
    teo_filter="on", teo_len=21, teo_smooth=7, teo_slope=6, teo_flat=0.02,
    teo_deep=0.50, teo_active=0.25, teo_dead=0.10,
    equity=10_000,
)
INDEXES = {
    "nasdaq": dict(name="Nasdaq-100", bench="QQQ", port=8050, min_n=90, max_n=110, checks="hourly",
                   momentum="21,126", breadth_min=0.30, vol_target=0.35,
                   cnbc=["https://www.cnbc.com/nasdaq-100/", "https://www.cnbc.com/markets/nasdaq-100/"],
                   wiki="https://en.wikipedia.org/wiki/Nasdaq-100", cache="ndx_constituents_cache.json"),
    "dow": dict(name="Dow 30", bench="DIA", port=8051, min_n=28, max_n=32, checks="close",
                momentum="21,63,126", breadth_min=0.40, vol_target=0.0,          # Dow: no vol target
                cnbc=["https://www.cnbc.com/dow-30/", "https://www.cnbc.com/markets/dow-30/"],
                wiki="https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average", cache="dow_constituents_cache.json"),
}
_arg = sys.argv[1].lower() if len(sys.argv) > 1 and sys.argv[1].lower() in INDEXES else None
INDEX_KEY = _arg or os.environ.get("DASH_INDEX", "nasdaq").lower()
CFG = INDEXES[INDEX_KEY]
BENCH = CFG["bench"]
CACHE_FILE = Path(__file__).with_name(CFG["cache"])
DEFAULTS["checks"] = CFG["checks"]                  # Nasdaq: hourly, Dow: at the close
DEFAULTS["momentum"] = CFG["momentum"]              # Nasdaq: 21 + 126 days, Dow: 21 + 63 + 126
DEFAULTS["breadth_min"] = CFG["breadth_min"]        # Nasdaq: 30%, Dow: 40%
DEFAULTS["vol_target"] = CFG["vol_target"]          # Nasdaq 35%, Dow off (always 100% deployed)
ET = ZoneInfo("America/New_York")
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0 Safari/537.36",
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# theme (dark, lavender text)
BG, CARD, BORDER = "#111318", "#1b1e27", "#2c3040"
TEXT, MUTED = "#c9b8ff", "#8f88b3"
GOOD, BAD, WARN, ACCENT = "#5fd3a3", "#ff7a8a", "#ffc46b", "#9d7bff"

# ───────────────────────── members (CNBC first) ─────────────────────────
NOT_STOCKS = {"NDX", "DJI", "SPX", "IXIC", "RUT", "VIX", "QQQ", "DIA", "SPY"}


def _clean(tickers):
    out = []
    for t in tickers:
        t = str(t).strip().upper()
        if not t or t[0] in ".@^" or "=" in t or re.fullmatch(r"US\d+[MY]", t):   # indexes, futures, bond yields
            continue
        if "." in t and len(t.split(".", 1)[1]) > 1:      # BTC.CM etc. (real share classes are one letter: BRK.B)
            continue
        t = t.replace(".", "-").replace("/", "-")                                      # BRK.B -> BRK-B (yfinance)
        if re.fullmatch(r"[A-Z][A-Z0-9\-]{0,6}", t) and t not in out and t not in NOT_STOCKS:
            out.append(t)
    return out


def _plausible(tickers):
    return CFG["min_n"] <= len(tickers) <= CFG["max_n"]


def _cnbc_candidates(html_text):
    """Ticker lists found on a CNBC index page, most reliable first."""
    found = []
    # 1. a member table in the HTML
    try:
        for t in pd.read_html(io.StringIO(html_text)):
            for c in t.columns:
                if str(c).strip().lower() in ("symbol", "ticker"):
                    found.append(("table", _clean(t[c].tolist())))
    except (ValueError, ImportError):
        pass
    # 2. links to quote pages (each member row links to /quotes/<SYMBOL>)
    found.append(("quote links", _clean(re.findall(r'href="(?:https://www\.cnbc\.com)?/quotes/([A-Za-z0-9.\-]+)', html_text))))
    # 3. symbols in the page's embedded JSON data
    found.append(("page data", _clean(re.findall(r'"symbol"\s*:\s*"([A-Za-z][A-Za-z0-9.\-]{0,6})"', html_text))))
    return found


def from_cnbc():
    errs = []
    for url in CFG["cnbc"]:
        try:
            r = requests.get(url, headers=UA, timeout=20)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            errs.append(f"{url}: {e}")
            continue
        for how, syms in _cnbc_candidates(r.text):
            if _plausible(syms):
                return dict(tickers=syms, weights={}, source=f"CNBC {CFG['name']} ({how})",
                            asof=dt.date.today().isoformat(), url=url)
        counts = ", ".join(f"{how} {len(syms)}" for how, syms in _cnbc_candidates(r.text))
        errs.append(f"{url}: no member list of {CFG['min_n']}-{CFG['max_n']} tickers found ({counts})")
    raise RuntimeError(" | ".join(errs))


def from_wikipedia():
    r = requests.get(CFG["wiki"], headers=UA, timeout=20)
    r.raise_for_status()
    for t in pd.read_html(io.StringIO(r.text)):
        for c in t.columns:
            if str(c).strip().lower() in ("ticker", "symbol"):
                tick = _clean(t[c].tolist())
                if _plausible(tick):
                    return dict(tickers=tick, weights={}, source=f"Wikipedia {CFG['name']} table",
                                asof=dt.date.today().isoformat())
    raise RuntimeError(f"{CFG['name']} table not found on Wikipedia")


def from_cache():
    d = json.loads(CACHE_FILE.read_text())
    d["source"] = f"saved list ({d.get('source', '?')}, {d.get('asof', '?')})"
    return d


SOURCES = {"cnbc": from_cnbc, "wikipedia": from_wikipedia}


def get_members(choice="auto"):
    order = ["cnbc", "wikipedia"] if choice == "auto" else [choice]
    notes = []
    for name in order:
        try:
            d = SOURCES[name]()
            try:
                CACHE_FILE.write_text(json.dumps(d, indent=1))
            except OSError:
                pass
            d["notes"] = notes
            return d
        except Exception as e:  # noqa: BLE001
            notes.append(f"{name}: {e}")
    if CACHE_FILE.exists():
        d = from_cache()
        d["notes"] = notes
        return d
    raise RuntimeError(f"Could not load {CFG['name']} members. " + " | ".join(notes))


# ───────────────────────── prices ─────────────────────────
def load_prices(tickers):
    yf_t = sorted(set(tickers) | {BENCH})
    data = yf.download(yf_t, period="3y", interval="1d", auto_adjust=True, progress=False,
                       group_by="column", threads=True)
    close = data["Close"] if isinstance(data.columns, pd.MultiIndex) else data[["Close"]]
    close = close.dropna(how="all", axis=1).sort_index()
    close.index = pd.to_datetime(close.index).tz_localize(None)
    return close


def load_hourly(tickers):
    """Completed hourly bars for the last ~8 days, indexed by bar END time (ET), like QuantConnect's hourly bars."""
    yf_t = sorted(set(tickers) | {BENCH})
    data = yf.download(yf_t, period="8d", interval="60m", auto_adjust=True, progress=False,
                       group_by="column", threads=True, prepost=False)
    h = data["Close"] if isinstance(data.columns, pd.MultiIndex) else data[["Close"]]
    h = h.dropna(how="all", axis=1).sort_index()
    idx = pd.to_datetime(h.index)
    idx = idx.tz_convert(ET) if idx.tz is not None else idx.tz_localize(ET)
    ends = pd.DatetimeIndex([min(t + pd.Timedelta(minutes=60), t.replace(hour=16, minute=0)) for t in idx])
    now = pd.Timestamp(dt.datetime.now(ET))
    h.index = ends
    return h[ends <= now]                                 # drop the hour still in progress


def split_completed(close):
    """Completed daily bars and the latest CLOSE. All checks are at-the-close: while the market is open, today's
    partial bar is ignored and everything uses yesterday's close."""
    now = dt.datetime.now(ET)
    last = close.index[-1].date()
    partial = last == now.date() and now.weekday() < 5 and dt.time(9, 30) <= now.time() < dt.time(16, 0)
    comp = close.iloc[:-1] if partial else close
    live = comp.ffill().iloc[-1]                     # latest completed close (not the intraday price)
    return comp, live, partial


# ───────────────────────── TEO (Trend Efficiency Oscillator) ─────────────────────────
def teo_frame(comp, n, smooth):
    """Signed Kaufman efficiency ratio over n days, EMA-smoothed - for every column."""
    path = comp.diff().abs().rolling(n).sum()
    er = (comp - comp.shift(n)) / path
    er = er.mask(path == 0, 0.0)
    if smooth > 1:
        er = er.apply(lambda col: col.dropna().ewm(span=smooth, adjust=False).mean().reindex(col.index))
    return er


TEO_STAGES = {0: "Dormant", 1: "Building", 2: "Flat", 3: "Extended", 4: "Exhausting", 5: "Fading"}


def teo_stage(teo, slope, flat, deep, active, dead):
    """Same as the algo. slope = sign(TEO) x (TEO now - TEO slope-days ago): + strengthening, - weakening."""
    mag = abs(teo)
    if mag < dead:
        return 0
    if slope > flat:
        return 3 if mag >= deep else 1
    if slope < -flat:
        return 5 if mag < active else 4
    return 3 if mag >= deep else 2


def teo_removed(stage):
    """True if a qualifying stock is removed before ranking: TEO Exhausting."""
    return stage == 4


# ───────────────────────── signals (mirror of the algo) ─────────────────────────
def lens(text):
    return [int(x) for x in str(text).split(",") if str(x).strip()]


def signals(close, p, members, hourly=None):
    comp, live, partial = split_completed(close)
    use_hourly = p.get("checks") == "hourly" and hourly is not None and not hourly.empty
    if use_hourly:
        live = hourly.ffill().iloc[-1].reindex(comp.columns).fillna(live)    # latest completed hourly close
    mom_lens = lens(p["momentum"])
    names = [t for t in members if t in comp.columns]
    rows = []
    for t in names:
        s = comp[t].dropna()
        px = live.get(t, np.nan)
        need = max([p["slow"], p["fast"]] + [n + 1 for n in mom_lens])
        ready = len(s) >= need and px > 0 and not math.isnan(px)
        r = dict(Ticker=t, Price=px, Ready=ready)
        if ready:
            r["SMA fast"] = s.iloc[-p["fast"]:].mean()
            r["SMA slow"] = s.iloc[-p["slow"]:].mean()
            for n in mom_lens:
                r[f"{n}d %"] = (s.iloc[-1] / s.iloc[-1 - n] - 1) * 100
            moms = [r[f"{n}d %"] for n in mom_lens]
            ok = r["SMA fast"] > r["SMA slow"] and px > r["SMA slow"]
            if p["mom_positive"] == "blend":
                ok = ok and np.mean(moms) > 0
            elif p["mom_positive"] == "all":
                ok = ok and min(moms) > 0
            r["Qualifies"] = ok
            r["Below 200d (stop)"] = px < r["SMA slow"]
            r["Vs 200d %"] = (px / r["SMA slow"] - 1) * 100
        rows.append(r)
    df = pd.DataFrame(rows)
    ready = df[df.Ready] if not df.empty else df
    # breadth = qualifying / ALL members (a member with too little history or no price counts as not qualifying)
    # count with == True: a column mixing True/False/NaN is "object" type, where summing numpy bools does OR, not +
    n_q = int((ready["Qualifies"] == True).sum()) if n_ready(ready) else 0  # noqa: E712
    n = len(members)
    share = n_q / n if n else 0.0
    not_ready = [t for t in members if t not in set(ready["Ticker"]) ] if not df.empty else list(members)

    cands = ready[ready["Qualifies"] == True].copy() if n_ready(ready) else ready  # noqa: E712

    # TEO for every member (shown in the table)
    teo = teo_frame(comp, p["teo_len"], p["teo_smooth"])
    t_now = teo.iloc[-1]
    t_past = teo.iloc[-1 - p["teo_slope"]] if len(teo) > p["teo_slope"] else t_now * np.nan
    t_slope = np.sign(t_now) * (t_now - t_past)          # + = strengthening, - = weakening (direction-aware)
    stages = {t: (teo_stage(t_now[t], t_slope[t], p["teo_flat"], p["teo_deep"], p["teo_active"], p["teo_dead"])
                  if pd.notna(t_now.get(t)) and pd.notna(t_slope.get(t)) else None) for t in t_now.index}
    if not df.empty:
        df["TEO"] = df["Ticker"].map(t_now)
        df["TEO slope"] = df["Ticker"].map(t_slope)
        df["TEO stage"] = df["Ticker"].map(lambda t: "" if stages.get(t) is None else TEO_STAGES[stages[t]])

    # TEO filter: qualifying stocks that are Exhausting are removed BEFORE ranking
    teo_out = []
    if p["teo_filter"] == "on" and not cands.empty:
        out_mask = cands["Ticker"].map(lambda t: teo_removed(stages.get(t)))
        teo_out = cands.loc[out_mask, "Ticker"].tolist()
        cands = cands[~out_mask]
    if not cands.empty:
        if p["mom_method"] == "rank":
            score = pd.Series(0.0, index=cands.index)
            for k in mom_lens:
                # same as the algo: sort strongest first, rank 1..N (ties keep list order)
                order = cands[f"{k}d %"].sort_values(ascending=False, kind="stable")
                score.loc[order.index] -= np.arange(1, len(order) + 1) / len(mom_lens)
            cands["Score"] = score
        else:
            cands["Score"] = cands[[f"{k}d %" for k in mom_lens]].mean(axis=1) / 100
        cands = cands.sort_values("Score", ascending=False, kind="stable")
        cands["Rank"] = np.arange(1, len(cands) + 1)
        df = df.merge(cands[["Ticker", "Score", "Rank"]], on="Ticker", how="left")
    else:
        df["Score"] = np.nan
        df["Rank"] = np.nan
    top = cands["Ticker"].head(p["top"]).tolist() if not cands.empty else []

    # market ETF (QQQ / DIA) vs its SMA
    q = comp[BENCH].dropna() if BENCH in comp else pd.Series(dtype=float)
    q_px = live.get(BENCH, np.nan)
    q_sma = q.iloc[-p["market_sma"]:].mean() if len(q) >= p["market_sma"] else np.nan

    # Filter trips since the last rebalance. The algo checks breadth and a FRESH index cross below its SMA at every
    # close; a trip puts it in cash until the next Monday rebalance, which resumes if breadth > breadth_min.
    now = dt.datetime.now(ET)
    monday = now.date() - dt.timedelta(days=now.weekday())
    before_reb = now.weekday() == 0 and now.time() < dt.time(9, 45)
    since = monday - dt.timedelta(days=7) if before_reb else monday          # date of the last rebalance
    preview = now.weekday() >= 5 or before_reb                              # next thing to happen is a rebalance
    trips = []
    if use_hourly:
        trips = hourly_trips(comp, hourly, members, p, since)
    else:
        bh = breadth_history(comp, members, p, days=10)
        hit = bh[(bh.index.date >= since) & (bh <= p["breadth_min"])]
        if len(hit):
            trips.append((hit.index[0].date(), f"breadth {hit.iloc[0]:.0%} at the close"))
        if len(q) > p["market_sma"]:
            sma_s = q.rolling(p["market_sma"]).mean()
            cross = (q.shift(1) >= sma_s.shift(1)) & (q < sma_s)
            cx = cross[cross & (cross.index.date >= since)]
            if len(cx):
                trips.append((cx.index[0].date(), f"{BENCH} closed below its {p['market_sma']}-day SMA (fresh cross)"))
    trips.sort()
    out_now = bool(trips) and not preview

    # vol target -> portfolio size
    basket_vol, size, vols = None, 1.0, {}
    vl = lens(p["vol_lookbacks"])
    if p["vol_target"] > 0 and top and vl:
        rets = comp[top].iloc[-(max(vl) + 1):].pct_change().iloc[1:].fillna(0.0)
        w = 1.0 / p["top"]
        for k in vl:
            r = rets.tail(k)
            if len(r) >= max(10, k // 2):
                vols[k] = float((r * w).sum(axis=1).std(ddof=1)) * math.sqrt(252)
        if vols and max(vols.values()) > 0:
            basket_vol = max(vols.values())
            size = min(1.0, max(p["vol_min_size"], p["vol_target"] / (p["leverage"] * basket_vol)))

    return dict(df=df, top=top, keep=top, teo_cash=[], teo_out=teo_out, teo=teo, n=n, n_q=n_q, share=share,
                not_ready=not_ready, risk_off=n > 0 and share <= p["breadth_min"],
                trips=trips, out_now=out_now, preview=preview, since=since, use_hourly=use_hourly,
                last_hour=hourly.index[-1] if use_hourly else None,
                q_px=q_px, q_sma=q_sma, basket_vol=basket_vol, vols=vols, size=size,
                partial=partial, bar_date=comp.index[-1].date(), comp=comp, live=live)


def hourly_trips(comp, hourly, members, p, since):
    """Filter trips on HOURLY bars since the last rebalance, like the algo with filter_check=hourly: each hourly close
    is compared with the daily SMAs / momentum as of the previous completed day."""
    names = [t for t in members if t in comp.columns and t in hourly.columns]
    c = comp[names]
    fast_d = c.rolling(p["fast"]).mean()
    slow_d = c.rolling(p["slow"]).mean()
    moms = [c / c.shift(n) - 1 for n in lens(p["momentum"])]
    blend_d = sum(moms) / len(moms)
    allpos_d = moms[0] > 0
    for m in moms[1:]:
        allpos_d &= m > 0
    q_d = comp[BENCH].dropna() if BENCH in comp else pd.Series(dtype=float)
    sma_d = q_d.rolling(p["market_sma"]).mean()
    out, above_prev, breadth_hit, cross_hit = [], None, None, None
    for ts, row in hourly.iterrows():
        d = ts.date()
        base = comp.index[comp.index.date < d]
        if not len(base):
            continue
        r = base[-1]
        # QQQ / DIA fresh cross below its SMA on an hourly close
        sma = sma_d.get(r, np.nan)
        qpx = row.get(BENCH, np.nan)
        if pd.notna(sma) and pd.notna(qpx):
            above = bool(qpx >= sma)                      # plain bool (numpy bools fail "is True")
            if d >= since and cross_hit is None and above_prev is True and not above:
                cross_hit = (d, f"{BENCH} {qpx:.2f} crossed below its {p['market_sma']}-day SMA {sma:.2f} "
                                f"at {ts:%a %H:%M}")
            above_prev = above
        # breadth on the hourly close
        if d >= since and breadth_hit is None:
            px = row.reindex(names)
            qual = (fast_d.loc[r] > slow_d.loc[r]) & (px > slow_d.loc[r])
            if p["mom_positive"] == "blend":
                qual &= blend_d.loc[r] > 0
            elif p["mom_positive"] == "all":
                qual &= allpos_d.loc[r]
            ready = slow_d.loc[r].notna() & blend_d.loc[r].notna() & px.notna()
            share = (qual & ready).sum() / max(len(members), 1)
            if share <= p["breadth_min"]:
                breadth_hit = (d, f"breadth {share:.0%} at {ts:%a %H:%M}")
    return [x for x in (breadth_hit, cross_hit) if x]


def n_ready(df):
    return not df.empty and "Qualifies" in df


def breadth_history(comp, members, p, days=260):
    names = [t for t in members if t in comp.columns]
    c = comp[names]
    fast = c.rolling(p["fast"]).mean()
    slow = c.rolling(p["slow"]).mean()
    moms = [c / c.shift(n) - 1 for n in lens(p["momentum"])]
    blend = sum(moms) / len(moms)
    q = (fast > slow) & (c > slow)
    if p["mom_positive"] == "blend":
        q &= blend > 0
    elif p["mom_positive"] == "all":
        for m in moms:
            q &= m > 0
    ready = slow.notna()
    for m in moms:
        ready &= m.notna()
    share = (q & ready).sum(axis=1) / max(len(members), 1)     # same as the card: divided by ALL members
    return share.tail(days)


# ───────────────────────── orders ─────────────────────────
def build_orders(sig, p, holdings):
    df = sig["df"].set_index("Ticker") if not sig["df"].empty else pd.DataFrame()
    per_name = p["equity"] * sig["size"] * p["leverage"] / p["top"]
    rows = []
    if sig["out_now"]:
        d0, why0 = sig["trips"][0]
        for h in holdings:
            rows.append(dict(Ticker=h, Action="SELL", Why=f"Filter tripped {d0:%a %b %d}: {why0} - cash until Monday"))
        return rows, 0.0
    if sig["risk_off"]:
        for h in holdings:
            rows.append(dict(Ticker=h, Action="SELL", Why="Breadth filter: risk-off, hold nothing"))
        return rows, 0.0
    for i, t in enumerate(sig["top"], 1):
        px = df.loc[t, "Price"]
        if t in sig["teo_cash"]:
            teo, stg = df.loc[t, "TEO"], df.loc[t, "TEO stage"]
            rows.append(dict(Ticker=t, Action="SELL -> CASH" if t in holdings else "CASH",
                             Why=f"Rank {i}, TEO {stg} ({teo:.2f})",
                             Price=round(float(px), 2), **{"Target $": 0, "Shares": 0, "Margin $": 0}))
            continue
        shares = math.floor(per_name / px) if px > 0 else 0
        act = "HOLD / RESIZE" if t in holdings else "BUY"
        rows.append(dict(Ticker=t, Action=act, Why=f"Rank {i}", Price=round(float(px), 2),
                         **{"Target $": round(per_name, 0), "Shares": shares,
                            "Margin $": round(per_name / max(p["leverage"], 1.0), 0)}))
    for h in holdings:
        if h in sig["top"]:
            continue
        why = "Not in top " + str(p["top"])
        if h not in df.index:
            why = f"No longer a {CFG['name']} member / no data"
        rows.append(dict(Ticker=h, Action="SELL", Why=why))
    return rows, per_name


# ───────────────────────── app ─────────────────────────
STATE = {"members": None, "members_choice": None, "members_time": None, "close": None, "hourly": None}


def card(title, value, sub="", color=TEXT):
    return html.Div([
        html.Div(title, style={"color": MUTED, "fontSize": "12px", "textTransform": "uppercase",
                               "letterSpacing": "0.05em"}),
        html.Div(value, style={"color": color, "fontSize": "24px", "fontWeight": 600, "marginTop": "4px"}),
        html.Div(sub, style={"color": MUTED, "fontSize": "12px", "marginTop": "2px"}),
    ], style={"background": CARD, "border": f"1px solid {BORDER}", "borderRadius": "10px",
              "padding": "12px 14px", "minWidth": "170px", "flex": "1"})


def field(label, comp):
    return html.Div([html.Label(label, style={"color": MUTED, "fontSize": "12px"}), comp],
                    style={"display": "flex", "flexDirection": "column", "gap": "3px"})


def num(id_, value, step):
    return dcc.Input(id=id_, type="number", value=value, step=step, debounce=True,
                     style={"width": "95px", "background": BG, "color": TEXT, "border": f"1px solid {BORDER}",
                            "borderRadius": "6px", "padding": "5px"})


def txt(id_, value, width="110px"):
    return dcc.Input(id=id_, type="text", value=value, debounce=True,
                     style={"width": width, "background": BG, "color": TEXT, "border": f"1px solid {BORDER}",
                            "borderRadius": "6px", "padding": "5px"})


def dd(id_, value, options, width="120px"):
    return dcc.Dropdown(id=id_, value=value, options=options, clearable=False,
                        style={"width": width, "color": "#222"})


TABLE_STYLE = dict(
    style_table={"overflowX": "auto"},
    style_header={"backgroundColor": "#232736", "color": TEXT, "fontWeight": 600, "border": f"1px solid {BORDER}"},
    style_cell={"backgroundColor": CARD, "color": TEXT, "border": f"1px solid {BORDER}", "padding": "5px 8px",
                "fontFamily": "Inter, Segoe UI, sans-serif", "fontSize": "13px", "textAlign": "right"},
    style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in ("Ticker", "Action", "Why")],
)


def fig_layout(fig, title, h=320):
    fig.update_layout(title=dict(text=title, font=dict(color=TEXT, size=15)), height=h,
                      paper_bgcolor=CARD, plot_bgcolor=CARD, font=dict(color=TEXT),
                      margin=dict(l=50, r=20, t=45, b=40), legend=dict(orientation="h", y=-0.15),
                      xaxis=dict(gridcolor=BORDER), yaxis=dict(gridcolor=BORDER))
    return fig


app = Dash(__name__, title=f"{CFG['name']} Momentum Signals")
app.layout = html.Div(style={"background": BG, "minHeight": "100vh", "padding": "18px 24px", "color": TEXT,
                             "fontFamily": "Inter, Segoe UI, sans-serif"}, children=[
    html.H2(f"{CFG['name']} Momentum + Trend - live signals", style={"margin": "0 0 4px 0"}),
    html.Div(id="sub", style={"color": MUTED, "fontSize": "13px", "marginBottom": "14px"}),

    html.Div(style={"display": "flex", "flexWrap": "wrap", "gap": "12px", "alignItems": "flex-end",
                    "background": CARD, "border": f"1px solid {BORDER}", "borderRadius": "10px",
                    "padding": "12px", "marginBottom": "14px"}, children=[
        field("Members from", dd("source", "auto", [
            {"label": "CNBC (auto backup)", "value": "auto"}, {"label": "CNBC only", "value": "cnbc"},
            {"label": "Wikipedia", "value": "wikipedia"}], "170px")),
        field("Account $", num("equity", DEFAULTS["equity"], 100)),
        field("Leverage", num("leverage", DEFAULTS["leverage"], 0.1)),
        field("Vol target", num("vol_target", DEFAULTS["vol_target"], 0.01)),
        field("Vol lookbacks", txt("vol_lookbacks", DEFAULTS["vol_lookbacks"], "80px")),
        field("Min size", num("vol_min_size", DEFAULTS["vol_min_size"], 0.05)),
        field("Top N", num("top", DEFAULTS["top"], 1)),
        field("Breadth min", num("breadth_min", DEFAULTS["breadth_min"], 0.01)),
        field("Momentum", txt("momentum", DEFAULTS["momentum"])),
        field("Mom method", dd("mom_method", DEFAULTS["mom_method"],
                               [{"label": "rank", "value": "rank"}, {"label": "return", "value": "return"}], "100px")),
        field("Mom positive", dd("mom_positive", DEFAULTS["mom_positive"],
                                 [{"label": x, "value": x} for x in ("blend", "all", "off")], "100px")),
        field("Fast / slow SMA", html.Div([num("fast", DEFAULTS["fast"], 1), num("slow", DEFAULTS["slow"], 1)],
                                          style={"display": "flex", "gap": "4px"})),
        field(f"{BENCH} SMA", num("market_sma", DEFAULTS["market_sma"], 1)),
        field("TEO filter", dd("teo_filter", DEFAULTS["teo_filter"],
                               [{"label": "on", "value": "on"}, {"label": "off", "value": "off"}], "80px")),
        field("TEO len / smooth / slope", html.Div([num("teo_len", DEFAULTS["teo_len"], 1),
                                                    num("teo_smooth", DEFAULTS["teo_smooth"], 1),
                                                    num("teo_slope", DEFAULTS["teo_slope"], 1)],
                                                   style={"display": "flex", "gap": "4px"})),
        field("TEO flat", num("teo_flat", DEFAULTS["teo_flat"], 0.005)),
        field("Market checks", dd("checks", DEFAULTS["checks"],
                                  [{"label": "hourly", "value": "hourly"}, {"label": "at the close", "value": "close"}],
                                  "120px")),
        field("Deep / active / dead", html.Div([num("teo_deep", DEFAULTS["teo_deep"], 0.05),
                                                num("teo_active", DEFAULTS["teo_active"], 0.05),
                                                num("teo_dead", DEFAULTS["teo_dead"], 0.01)],
                                               style={"display": "flex", "gap": "4px"})),

        field("Current holdings (comma separated)", txt("holdings", "", "260px")),
        html.Button("Refresh data", id="refresh", n_clicks=0,
                    style={"background": ACCENT, "color": "white", "border": "none", "borderRadius": "6px",
                           "padding": "8px 16px", "fontWeight": 600, "cursor": "pointer", "height": "34px"}),
    ]),

    dcc.Interval(id="tick", interval=60 * 60 * 1000, n_intervals=0),    # hourly auto refresh
    dcc.Loading(type="dot", color=ACCENT, children=[
        html.Div(id="error", style={"color": BAD, "marginBottom": "8px"}),
        html.Div(id="cards", style={"display": "flex", "flexWrap": "wrap", "gap": "10px", "marginBottom": "14px"}),
        html.H3("This week's orders", style={"margin": "6px 0"}),
        html.Div(id="orders"),
        html.Div(style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit, minmax(520px, 1fr))",
                        "gap": "12px", "marginTop": "14px"}, children=[
            dcc.Graph(id="g_corr"), dcc.Graph(id="g_breadth"), dcc.Graph(id="g_qqq"), dcc.Graph(id="g_scores"),
        ]),
        html.H3("All members", style={"margin": "16px 0 6px 0"}),
        html.Div(id="all"),
        html.Div(id="notes", style={"color": MUTED, "fontSize": "12px", "marginTop": "10px"}),
    ]),
])


@app.callback(
    Output("sub", "children"), Output("error", "children"), Output("cards", "children"),
    Output("orders", "children"), Output("g_corr", "figure"), Output("g_breadth", "figure"),
    Output("g_qqq", "figure"), Output("g_scores", "figure"), Output("all", "children"), Output("notes", "children"),
    Input("refresh", "n_clicks"), Input("tick", "n_intervals"), Input("checks", "value"),
    Input("source", "value"), Input("equity", "value"), Input("leverage", "value"), Input("vol_target", "value"),
    Input("vol_lookbacks", "value"), Input("vol_min_size", "value"), Input("top", "value"),
    Input("breadth_min", "value"), Input("momentum", "value"), Input("mom_method", "value"),
    Input("mom_positive", "value"), Input("fast", "value"), Input("slow", "value"), Input("market_sma", "value"),
    Input("holdings", "value"),
    Input("teo_filter", "value"), Input("teo_len", "value"), Input("teo_smooth", "value"),
    Input("teo_slope", "value"), Input("teo_flat", "value"), Input("teo_deep", "value"),
    Input("teo_active", "value"), Input("teo_dead", "value"),
    State("refresh", "n_clicks_timestamp"),
)
def update(n_clicks, n_tick, checks, source, equity, leverage, vol_target, vol_lookbacks, vol_min_size, top, breadth_min,
           momentum, mom_method, mom_positive, fast, slow, market_sma, holdings,
           teo_filter="on", teo_len=21, teo_smooth=7, teo_slope=6, teo_flat=0.02,
           teo_deep=0.50, teo_active=0.25, teo_dead=0.10, _ts=None):
    from dash import ctx
    empty = go.Figure()
    fig_layout(empty, "")
    try:
        p = dict(DEFAULTS)
        p.update(equity=float(equity or 0), leverage=float(leverage or 1), vol_target=float(vol_target or 0),
                 vol_lookbacks=vol_lookbacks or "21,63", vol_min_size=float(vol_min_size or 0),
                 top=max(int(top or 10), 1), breadth_min=float(breadth_min or 0), momentum=momentum or DEFAULTS["momentum"],
                 mom_method=mom_method, mom_positive=mom_positive, fast=int(fast or 10), slow=int(slow or 200),
                 market_sma=int(market_sma or 200), teo_filter=teo_filter or "on",
                 teo_len=int(teo_len or 21), teo_smooth=int(teo_smooth or 1), teo_slope=max(int(teo_slope or 6), 1),
                 teo_flat=float(teo_flat or 0), teo_deep=float(teo_deep if teo_deep is not None else 0.5),
                 teo_active=float(teo_active if teo_active is not None else 0.25),
                 teo_dead=float(teo_dead if teo_dead is not None else 0.1), checks=checks or DEFAULTS["checks"])
        try:
            trig = ctx.triggered_id
        except Exception:  # noqa: BLE001  (called outside a callback)
            trig = None
        force = trig == "refresh"                       # Refresh button: members + prices
        reprice = force or trig == "tick"               # hourly tick: prices only
        stale = STATE["members_time"] is None or (dt.datetime.now() - STATE["members_time"]).total_seconds() > 12 * 3600
        if STATE["members"] is None or STATE["members_choice"] != source or stale or force:
            STATE["members"] = get_members(source)
            STATE["members_choice"] = source
            STATE["members_time"] = dt.datetime.now()
            STATE["close"] = None
        mem = STATE["members"]
        if STATE["close"] is None or reprice:
            STATE["close"] = load_prices(mem["tickers"])
            STATE["hourly"] = None
        close = STATE["close"]
        hourly, hourly_err = None, ""
        if p["checks"] == "hourly":
            if STATE.get("hourly") is None or reprice:
                try:
                    STATE["hourly"] = load_hourly(mem["tickers"])
                except Exception as e:  # noqa: BLE001
                    STATE["hourly"], hourly_err = None, str(e)
            hourly = STATE.get("hourly")

        sig = signals(close, p, mem["tickers"], hourly)
        held = _clean((holdings or "").replace(";", ",").replace(" ", ",").split(","))
        orders, per_name = build_orders(sig, p, held)

        # ── header + cards
        missing = [t for t in mem["tickers"] if t not in close.columns]
        src_txt = (f"{len(mem['tickers'])} members from {mem['source']}"
                   f"{' (as of ' + mem['asof'] + ')' if mem.get('asof') else ''}")
        from_cnbc = str(mem["source"]).startswith("CNBC")
        sub = [html.Span(src_txt if from_cnbc else f"⚠ NOT from CNBC - {src_txt}",
                         style={"color": TEXT if from_cnbc else BAD, "fontWeight": 400 if from_cnbc else 600}),
               html.Span(f"  ·  prices via yfinance  ·  indicators through {sig['bar_date']}"
                         f"{('  ·  checks HOURLY: price = hourly close ' + format(sig['last_hour'], '%a %H:%M')) if sig['use_hourly'] else ('  ·  market open: using the last close (checks are at the close)' if sig['partial'] else '')}"
                         f"  ·  updated {dt.datetime.now():%Y-%m-%d %H:%M}")]
        q_above = sig["q_px"] >= sig["q_sma"] if not math.isnan(sig["q_sma"]) else None
        out = sig["risk_off"] or sig["out_now"]
        gross = (sig["size"] * p["leverage"] * len(sig["keep"]) / p["top"]) if not out else 0.0
        exp_vol = (sig["basket_vol"] or 0) * gross
        cards = [
            (card("Regime", "CASH until Monday",
                  f"filter tripped {sig['trips'][0][0]:%a %b %d}: {sig['trips'][0][1]}", BAD) if sig["out_now"] else
             card("Regime", "RISK-OFF" if sig["risk_off"] else "RISK-ON",
                  ("breadth at/below minimum - hold nothing" if sig["risk_off"] else "breadth filter passes")
                  + (" · next rebalance decides" if sig["preview"] else ""),
                  BAD if sig["risk_off"] else GOOD)),
            card("Since last rebalance", "No filter trips" if not sig["trips"] else "Filter tripped",
                 (f"checked every {'hour' if sig['use_hourly'] else 'close'} since {sig['since']:%a %b %d}"
                  if not sig["trips"] else
                  " · ".join(f"{d_:%a}: {w_}" for d_, w_ in sig["trips"]))
                 + (" · Monday's rebalance resumes if breadth > min" if sig["trips"] and sig["preview"] else ""),
                 GOOD if not sig["trips"] else (WARN if sig["preview"] else BAD)),
            card("Breadth", f"{sig['share']:.0%}", f"{sig['n_q']} of {sig['n']} members qualify · min {p['breadth_min']:.0%}"
                 + (f" · {len(sig['not_ready'])} without enough data" if sig["not_ready"] else ""),
                 BAD if sig["risk_off"] else (WARN if sig["share"] <= p["breadth_min"] + 0.05 else GOOD)),
            card(f"{BENCH} vs {p['market_sma']}d SMA",
                 f"{(sig['q_px'] / sig['q_sma'] - 1):+.1%}" if q_above is not None else "n/a",
                 f"{sig['q_px']:.2f} vs {sig['q_sma']:.2f}" + ("" if q_above or q_above is None
                                                               else " · a fresh cross below stops the algo"),
                 GOOD if q_above else BAD),
            card("Basket vol (1x)", f"{sig['basket_vol']:.0%}" if sig["basket_vol"] else "n/a",
                 " · ".join(f"{k}d {v:.0%}" for k, v in sig["vols"].items()) + " · higher one used"),
            card("Portfolio size", f"{(0 if sig['risk_off'] else sig['size']):.0%}",
                 f"target {p['vol_target']:.0%} ÷ ({p['leverage']}x × basket vol)", ACCENT),
            card("Gross exposure", f"{gross:.2f}x", f"${p['equity'] * gross:,.0f} of positions · "
                                                   f"${per_name:,.0f} per name"),
            card("Expected account vol", f"{exp_vol:.0%}", f"target {p['vol_target']:.0%}"
),
            card("TEO removed", f"{len(sig['teo_out'])} of {sig['n_q']}" if p["teo_filter"] == "on" else "off",
                 "qualifying stocks Exhausting - skipped before ranking"
                 + (f" · {len(sig['top'])} picks left" if len(sig["top"]) < p["top"] else ""),
                 WARN if sig["teo_out"] else GOOD),
        ]

        # ── orders table
        ocols = ["Ticker", "Action", "Why", "Price", "Target $", "Shares", "Margin $"]
        orders_tbl = dash_table.DataTable(
            data=orders, columns=[{"name": c, "id": c} for c in ocols], **TABLE_STYLE,
            style_data_conditional=[
                {"if": {"filter_query": '{Action} = "BUY"', "column_id": "Action"}, "color": GOOD, "fontWeight": 600},
                {"if": {"filter_query": '{Action} = "SELL"', "column_id": "Action"}, "color": BAD, "fontWeight": 600},
                {"if": {"filter_query": '{Action} contains "CASH"', "column_id": "Action"}, "color": WARN,
                 "fontWeight": 600},
                {"if": {"filter_query": '{Why} contains "STOP"', "column_id": "Why"}, "color": WARN},
            ]) if orders else html.Div("No orders - nothing qualifies and no holdings entered.", style={"color": MUTED})

        # ── correlation of this week's picks (concentration check)
        top = sig["top"]
        if len(top) >= 2:
            r = sig["comp"][top].pct_change().tail(63)
            cm = r.corr().round(2)
            avg = (cm.values.sum() - len(top)) / (len(top) ** 2 - len(top))
            g_corr = go.Figure(go.Heatmap(z=cm.values, x=top, y=top, zmin=-1, zmax=1, colorscale="RdBu_r",
                                          text=cm.values, texttemplate="%{text}", textfont=dict(size=10)))
            fig_layout(g_corr, f"Picks: 63-day correlation (average {avg:.2f}) - high = one bet", 380)
        else:
            g_corr = fig_layout(go.Figure(), "Picks: correlation (needs 2+ picks)", 380)

        # ── breadth history
        bh = breadth_history(sig["comp"], mem["tickers"], p)
        g_b = go.Figure(go.Scatter(x=bh.index, y=bh * 100, mode="lines", line=dict(color=ACCENT), name="Breadth"))
        g_b.add_hline(y=p["breadth_min"] * 100, line=dict(color=BAD, dash="dash"),
                      annotation_text=f"risk-off at {p['breadth_min']:.0%}", annotation_font_color=BAD)
        fig_layout(g_b, "Breadth, last year (% qualifying, today's member list)", 380)
        g_b.update_yaxes(ticksuffix="%")

        # ── market ETF vs SMA
        q = sig["comp"][BENCH].dropna()
        qs = q.rolling(p["market_sma"]).mean()
        g_q = go.Figure([go.Scatter(x=q.index[-260:], y=q.tail(260), name=BENCH, line=dict(color=TEXT)),
                         go.Scatter(x=qs.index[-260:], y=qs.tail(260), name=f"{p['market_sma']}d SMA",
                                    line=dict(color=WARN, dash="dot"))])
        fig_layout(g_q, f"{BENCH} vs its {p['market_sma']}-day SMA", 380)

        # ── momentum of the top candidates
        df = sig["df"]
        mcols = [f"{n}d %" for n in lens(p["momentum"])]
        cand = df.dropna(subset=["Rank"]).sort_values("Rank").head(max(p["top"] * 2, 20))
        g_s = go.Figure()
        for c, col in zip(mcols, [ACCENT, GOOD, WARN, BAD]):
            g_s.add_bar(x=cand["Ticker"], y=cand[c], name=c, marker_color=col)
        if len(cand) > p["top"]:
            g_s.add_vline(x=p["top"] - 0.5, line=dict(color=TEXT, dash="dash"))
        fig_layout(g_s, f"Momentum of the top {len(cand)} candidates (left of the line = picks)", 380)
        g_s.update_layout(barmode="group")
        g_s.update_yaxes(ticksuffix="%")

        # ── all members table
        show = df.copy()
        for c in ["Price", "SMA fast", "SMA slow"]:
            if c in show:
                show[c] = show[c].round(2)
        for c in mcols + ["Vs 200d %"]:
            if c in show:
                show[c] = show[c].round(1)
        if "Score" in show:
            show["Score"] = show["Score"].round(3)
        for c in ("TEO", "TEO slope"):
            if c in show:
                show[c] = show[c].round(3)
        for c in ("Qualifies", "Ready"):
            if c in show:
                show[c] = show[c].map({True: "yes", False: "no"}).fillna("")
        show["Pick"] = show["Ticker"].map(lambda t: "★" if t in top else ("TEO out" if t in sig["teo_out"] else ""))
        w = mem.get("weights") or {}
        if any(v is not None for v in w.values()):
            show["Index wt %"] = show["Ticker"].map(lambda t: w.get(t))
        order_cols = [c for c in ["Pick", "Rank", "Ticker", "Price", "Qualifies", "Score"] + mcols +
                      ["TEO", "TEO slope", "TEO stage", "SMA fast", "SMA slow", "Vs 200d %",
                       "Index wt %", "Ready"] if c in show]
        show = show[order_cols].sort_values(["Rank", "Ticker"], na_position="last")
        all_tbl = dash_table.DataTable(
            data=show.to_dict("records"), columns=[{"name": c, "id": c} for c in order_cols],
            sort_action="native", filter_action="native", page_size=25, **TABLE_STYLE,
            style_data_conditional=[
                {"if": {"filter_query": '{Pick} = "★"'}, "backgroundColor": "#2a2342"},
                {"if": {"filter_query": '{Pick} = "TEO out"'}, "backgroundColor": "#3a2f1f"},
                {"if": {"filter_query": '{TEO stage} = "Exhausting"',
                        "column_id": "TEO stage"}, "color": WARN},
                {"if": {"filter_query": '{TEO stage} = "Building" || {TEO stage} = "Extended"',
                        "column_id": "TEO stage"}, "color": GOOD},
                {"if": {"filter_query": '{Qualifies} = "no"', "column_id": "Qualifies"}, "color": BAD},
                {"if": {"filter_query": '{Qualifies} = "yes"', "column_id": "Qualifies"}, "color": GOOD},
            ])

        notes = []
        if p["checks"] == "hourly" and not sig["use_hourly"]:
            notes.append(f"Hourly prices could not be loaded ({hourly_err or 'no data'}) - showing at-the-close checks.")
        if mem.get("notes"):
            notes.append("Sources that failed before this one: " + " | ".join(mem["notes"]))
        if missing:
            notes.append(f"No yfinance prices for: {', '.join(missing)}")
        if sig["not_ready"]:
            notes.append(f"Counted as not qualifying in breadth (no price or under {p['slow']} days of history): "
                         f"{', '.join(sig['not_ready'])}")
        notes.append(f"Same rules as the latest algo. Differences: breadth here divides by ALL members (the algo divides by "
                     f"members with enough history); the breadth chart uses today's member list for the whole year; "
                     f"market holidays are not skipped when working out 'since last rebalance'.")
        return (sub, "", cards, orders_tbl, g_corr, g_b, g_q, g_s, all_tbl,
                [html.Div(n) for n in notes])
    except Exception as e:  # noqa: BLE001
        return ("", f"Error: {e}", [], "", empty, empty, empty, empty, "", "")


def run():
    print(f"{CFG['name']} dashboard -> http://127.0.0.1:{CFG['port']}")
    app.run(debug=False, port=CFG["port"])


if __name__ == "__main__":
    run()
