# region imports
from AlgorithmImports import *
# endregion

# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
#  DOW 30 - MOMENTUM + TREND + TEO FILTER                              QuantConnect / LEAN, Python
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
#  Same engine as the QQQ top-30 algo. Differences: the universe (Dow 30), DIA as benchmark and market filter,
#  a TEO filter in selection, market checks at the daily close, no per-stock stop, and no vol target by default.
#
#  Universe   universe=etf (default): point-in-time DIA members; a stock leaving the Dow is sold ("Left the Dow").
#             universe=static: today's list (DOW30 below) for the whole backtest.
#  Data       Hourly bars. Trend, momentum, TEO and vol use DAILY bars.
#  Qualify    10-day SMA > 200-day SMA, price > 200-day SMA, and momentum positive
#             (mom_positive: blend = average of the lookback returns > 0, all = every lookback > 0, off).
#  TEO        teo_filter=on: at each rebalance every qualifier gets the Trend Efficiency Oscillator stage (same as
#  filter     the TradingView TEO, 21 / 7 / 6: signed Kaufman efficiency ratio over teo_len days, EMA-smoothed
#             by teo_smooth). Stocks in the EXHAUSTING stage are dropped BEFORE ranking, so the next-best moves
#             up. Breadth ignores TEO. Stages, with slope = sign(TEO) x (TEO now - TEO teo_slope days ago):
#               |TEO| < teo_dead                 -> Dormant
#               slope > +teo_flat                -> Extended if |TEO| >= teo_deep, else Building
#               slope < -teo_flat                -> Fading if |TEO| < teo_active, else Exhausting
#               otherwise                        -> Extended if |TEO| >= teo_deep, else Flat
#  Select     Top `top` (10) by momentum over the `momentum` lookbacks (21, 63, 126 days):
#             mom_method=rank averages each stock's rank on each lookback; return averages the returns.
#             Fewer than 10 left -> the empty slots stay in cash.
#  Size       Each pick gets size x leverage / 10 of the account. Leverage is fixed (`leverage`).
#             Vol target (vol_target > 0; default 0 = off, always 100% deployed) sets SIZE, the share of the
#             account deployed: size = vol_target / (leverage x basket vol), capped at 100%, floored at
#             vol_min_size. Basket vol = realized vol of the exact basket being bought (equal weights, at 1x),
#             the HIGHER of the `vol_lookbacks` (21 / 63 days).
#  Rebalance  Weekly, 15 minutes after Monday's open (Tuesday after a holiday); rebalance=daily for every day.
#             No per-stock stop: holdings change only at the rebalance or when the market filter trips.
#  Market     Kill switch; sells everything. filter_check=close (default): checked on the 4:00 PM bar only, the
#  filter     sell fills at the next open; hourly: every hourly bar.
#               breadth = share of the Dow that qualifies. market_filter=both (default) trips when breadth is at or
#               below `breadth_min` (40%) OR DIA makes a FRESH close below its `market_sma` (200-day SMA).
#               Trading resumes at a rebalance once breadth is above breadth_min, even if DIA is still below.
#             market_filter=breadth: breadth only.  sma: out while DIA is below its SMA.  off: no filter.
#  Kalshi     kalshi=on models Kalshi stock perps:
#             - Fee: kalshi_fee (0.02%) of the position value on every open and close.
#             - Funding, once a day at 4:00 PM ET on the position value (longs pay a positive rate). No market
#               history exists, so it is modeled as carry. funding_mode=rates: risk-free rate + funding_spread;
#               fixed: funding_annual; off. Optional funding_cap / funding_floor per payment (0 = off; crypto
#               perps use 2% / 0.01%). Prices are dividend-adjusted, so dividends are not subtracted again.
#               No margin interest is charged - with a perp the leverage cost IS the funding.
#             - Interest Kalshi pays on the margin account, daily on the end-of-day balance:
#               promo (default): interest_pos_apy (7%) on margin backing positions (position value / leverage)
#                 when leverage >= promo_min_lev (1.5x), interest_cash_apy (3.5%) on the rest
#                 (Kalshi promo of Oct 29, 2026; $250 average balance minimum).
#               promo_rates: same split, tracking history (fairer for 2011-2021 near-zero rates):
#                 cash = risk-free - interest_haircut (>= 0), positions = that + (7% - 3.5%).
#               rates: risk-free - interest_haircut on everything.  fixed: interest_apy.  off.
#               The 7% is a promotion with capacity caps and only covers eligible stocks/ETFs.
#
#  Parameters (QuantConnect "Parameters" panel; defaults shown)
#    fast=10  slow=200  momentum=21,63,126  mom_method=rank|return  mom_positive=blend|all|off  top=10
#    leverage=1.0  universe=etf|static  start_year=2010  rebalance=weekly|daily
#    vol_target=0 (off)  vol_lookbacks=21,63  vol_min_size=0
#    teo_filter=on|off  teo_len=21  teo_smooth=7  teo_slope=6  teo_flat=0.02  teo_deep=0.50  teo_active=0.25  teo_dead=0.10
#    market_filter=both|breadth|sma|off  breadth_min=0.40  filter_check=close|hourly  market_sma=200
#    kalshi=on|off  kalshi_fee=0.0002  funding_mode=rates|fixed|off  funding_spread=0  funding_annual=0.05
#    funding_cap=0  funding_floor=0  interest_mode=promo|promo_rates|rates|fixed|off  interest_pos_apy=0.07
#    interest_cash_apy=0.035  promo_min_lev=1.5  interest_haircut=0.005  interest_apy=0.0325
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

INDEX_ETF = "DIA"

# Current Dow 30 (after Alphabet replaced Verizon on June 29, 2026). Only used when universe=static.
DOW30 = ["AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
         "GOOGL", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM",
         "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "WMT"]

TEO_STAGES = {0: "Dormant", 1: "Building", 2: "Flat", 3: "Extended", 4: "Exhausting", 5: "Fading"}
EXHAUSTING = 4


def ints(text):
    """'21,63,126' -> [21, 63, 126]"""
    return [int(x) for x in str(text).split(",") if x.strip()]


def teo_stage(teo, slope, flat, deep, active, dead):
    """TEO stage number (see TEO_STAGES). slope > 0 = strengthening, < 0 = weakening."""
    mag = abs(teo)
    if mag < dead:
        return 0
    if slope > flat:
        return 3 if mag >= deep else 1
    if slope < -flat:
        return 5 if mag < active else 4
    return 3 if mag >= deep else 2


class PercentOfNotionalFeeModel(FeeModel):
    """Kalshi-style fee: a % of the position value on every open and close."""

    def __init__(self, rate):
        super().__init__()
        self.rate = rate

    def get_order_fee(self, parameters):
        value = abs(parameters.order.absolute_quantity) * parameters.security.price * self.rate
        return OrderFee(CashAmount(value, "USD"))


class SymbolData:
    """Daily fast / slow SMAs and momentum (rate of change) for one stock."""

    def __init__(self, algo, symbol, fast, slow, mom_lens):
        self.symbol = symbol
        self.fast = algo.sma(symbol, fast, Resolution.DAILY)
        self.slow = algo.sma(symbol, slow, Resolution.DAILY)
        self.moms = [algo.rocp(symbol, n, Resolution.DAILY) for n in mom_lens]
        for ind in self.indicators():
            algo.warm_up_indicator(symbol, ind, Resolution.DAILY)

    def indicators(self):
        return [self.fast, self.slow] + self.moms

    @property
    def ready(self):
        return all(ind.is_ready for ind in self.indicators())

    def mom_values(self):
        return [m.current.value for m in self.moms]


class Dow30MomentumTrend(QCAlgorithm):

    # ───────── setup ─────────
    def initialize(self):
        p = self.get_parameter
        # signals
        self.fast_len = int(p("fast", 10))
        self.slow_len = int(p("slow", 200))
        self.mom_lens = ints(p("momentum", "21,63,126"))
        self.mom_method = p("mom_method", "rank")                    # "rank" or "return"
        self.mom_positive = p("mom_positive", "blend")               # "blend", "all" or "off"
        self.top_n = int(p("top", 10))
        self.rebal_mode = p("rebalance", "weekly")                   # "weekly" or "daily"
        self.universe_mode = p("universe", "etf")                    # "etf" (point-in-time) or "static"
        # TEO filter
        self.teo_on = p("teo_filter", "on") == "on"
        self.teo_len = int(p("teo_len", 21))
        self.teo_smooth = int(p("teo_smooth", 7))
        self.teo_slope = int(p("teo_slope", 6))
        self.teo_flat = float(p("teo_flat", 0.02))
        self.teo_deep = float(p("teo_deep", 0.50))
        self.teo_active = float(p("teo_active", 0.25))
        self.teo_dead = float(p("teo_dead", 0.10))
        # sizing
        self.gross = float(p("leverage", 1.0))
        self.vol_target = float(p("vol_target", 0.0))                # annual account vol; 0 = off
        self.vol_lens = ints(p("vol_lookbacks", "21,63"))
        self.vol_min_size = float(p("vol_min_size", 0.0))
        # market filter
        self.mkt_mode = p("market_filter", "both")                   # "both", "breadth", "sma" or "off"
        if self.mkt_mode == "on":                                    # old name for "sma"
            self.mkt_mode = "sma"
        self.breadth_min = float(p("breadth_min", 0.40))             # trade only while MORE than this qualify
        self.filter_check = p("filter_check", "close")               # "close" (4:00 PM bar) or "hourly"
        self.mkt_len = int(p("market_sma", self.slow_len))
        self.risk_off = False
        self.idx_above = None                                        # last DIA close vs its SMA ("both")
        # Kalshi
        self.kalshi = p("kalshi", "on") == "on"
        self.kalshi_fee = float(p("kalshi_fee", 0.0002))
        self.fund_mode = p("funding_mode", "rates")                  # "rates", "fixed" or "off"
        self.fund_spread = float(p("funding_spread", 0.0))
        self.fund_annual = float(p("funding_annual", 0.05))
        self.fund_cap = float(p("funding_cap", 0.0))
        self.fund_floor = float(p("funding_floor", 0.0))
        self.int_mode = p("interest_mode", "promo")                  # "promo", "promo_rates", "rates", "fixed", "off"
        self.int_pos = float(p("interest_pos_apy", 0.07))
        self.int_cash = float(p("interest_cash_apy", 0.035))
        self.promo_min_lev = float(p("promo_min_lev", 1.5))
        self.int_haircut = float(p("interest_haircut", 0.005))
        self.int_apy = float(p("interest_apy", 0.0325))
        self.int_total = self.fund_total = 0.0
        self.last_carry = None                                       # date of the last funding / interest payment

        self.set_start_date(int(p("start_year", 2010)), 1, 1)
        self.set_cash(10_000)
        self.set_brokerage_model(BrokerageName.INTERACTIVE_BROKERS_BROKERAGE, AccountType.MARGIN)
        self.settings.free_portfolio_value_percentage = 0.02         # cash buffer for fees and slippage

        # each security needs a margin allowance above the gross target or orders are rejected
        # (a real Reg T account allows 2x overnight; more needs portfolio margin or futures / perps)
        self.sec_leverage = max(self.gross + 1.0, 3.0)
        self.universe_settings.resolution = Resolution.HOUR
        self.universe_settings.leverage = self.sec_leverage

        self.spy = self.add_equity("SPY", Resolution.HOUR).symbol     # market clock for scheduling
        self.idx = self.add_equity(INDEX_ETF, Resolution.HOUR).symbol
        self.set_benchmark(INDEX_ETF)
        self.idx_sma = self.sma(self.idx, self.mkt_len, Resolution.DAILY)
        self.warm_up_indicator(self.idx, self.idx_sma, Resolution.DAILY)

        self.data = {}
        if self.universe_mode == "etf":
            self.add_universe(self.universe.etf(INDEX_ETF, self.universe_settings, self.etf_filter))
        else:
            for t in DOW30:
                self.add_equity(t, Resolution.HOUR, leverage=self.sec_leverage)

        if self.kalshi and (self.fund_mode != "off" or self.int_mode != "off"):
            self.schedule.on(self.date_rules.every_day(self.spy), self.time_rules.at(16, 0), self.daily_carry)
        days = self.date_rules.week_start(self.spy) if self.rebal_mode == "weekly" else self.date_rules.every_day(self.spy)
        self.schedule.on(days, self.time_rules.after_market_open(self.spy, 15), self.rebalance)

    # ───────── universe ─────────
    def etf_filter(self, constituents):
        """All DIA holdings, point-in-time."""
        return [c.symbol for c in constituents]

    def on_securities_changed(self, changes):
        for sec in changes.added_securities:
            sym = sec.symbol
            if sym in (self.spy, self.idx) or sym in self.data:
                continue
            sec.set_leverage(self.sec_leverage)
            if self.kalshi:
                sec.set_fee_model(PercentOfNotionalFeeModel(self.kalshi_fee))
            self.data[sym] = SymbolData(self, sym, self.fast_len, self.slow_len, self.mom_lens)
        for sec in changes.removed_securities:
            sym = sec.symbol
            if sym in (self.spy, self.idx):
                continue
            if self.portfolio[sym].invested:
                self.liquidate(sym, tag="Left the Dow")
            sd = self.data.pop(sym, None)
            if sd:
                for ind in sd.indicators():
                    self.deregister_indicator(ind)

    # ───────── market filter (no per-stock stop) ─────────
    def on_data(self, slice: Slice):
        if self.is_warming_up:
            return
        bar = slice.bars.get(self.idx)
        at_close = self.time.hour == 16 and self.time.minute == 0
        if bar is not None and (self.filter_check != "close" or at_close):
            self.market_trips(bar)

    def market_trips(self, bar):
        """Kill switch on a DIA bar. Sells everything and returns True if it trips."""
        sma = self.idx_sma
        if self.mkt_mode == "sma":
            if sma.is_ready and not self.risk_off and bar.close < sma.current.value:
                return self.go_risk_off(f"MARKET FILTER: {INDEX_ETF} {bar.close:.2f} below {self.mkt_len}-day SMA {sma.current.value:.2f}")
            return False
        if self.mkt_mode in ("breadth", "both") and not self.risk_off:
            share, n_q, n = self.breadth()
            if n and share <= self.breadth_min:
                return self.go_risk_off(f"BREADTH FILTER: only {n_q} of {n} stocks qualify ({share:.0%})")
        if self.mkt_mode == "both" and sma.is_ready:
            above = bar.close >= sma.current.value
            crossed_below = self.idx_above is True and not above
            self.idx_above = above
            if crossed_below and not self.risk_off:
                return self.go_risk_off(f"SMA FILTER: {INDEX_ETF} {bar.close:.2f} crossed below its {self.mkt_len}-day SMA {sma.current.value:.2f}")
        return False

    def go_risk_off(self, tag):
        self.risk_off = True
        self.liquidate(tag=tag)
        return True

    # ───────── weekly rebalance ─────────
    def rebalance(self):
        if self.is_warming_up or not self.market_allows():
            return
        cands, teo_out = self.teo_filter(self.candidates())
        top = self.ranked(cands)[: self.top_n]
        size, basket_vol = self.target_size(top, self.daily_closes(top))
        weight = size * self.gross / self.top_n
        tag = ("Weekly" if self.rebal_mode == "weekly" else "Daily") + " rebalance"
        if self.vol_target > 0:
            tag += f" (size {size:.0%}, basket vol {basket_vol:.0%})" if basket_vol else f" (size {size:.0%}, no vol data)"
        if self.teo_on:
            tag += f" | TEO removed {len(teo_out)} of {len(teo_out) + len(cands)}"
            if teo_out:
                self.debug(f"{self.time:%Y-%m-%d} TEO removed: " + ", ".join(
                    f"{sym.value} {teo:.2f} {TEO_STAGES[stage]}" for sym, teo, stage in teo_out))
        if top:
            self.set_holdings([PortfolioTarget(sym, weight) for sym in top], liquidate_existing_holdings=True, tag=tag)
        elif self.portfolio.invested:
            self.liquidate(tag="TEO filter: no qualifying stock passed TEO - all cash")

        self.plot("Positions", "Holdings", len(top))
        self.plot("TEO filter", "Candidates removed", len(teo_out))
        self.plot("Vol target", "Portfolio size (% of account)", size * 100)
        if basket_vol:
            self.plot("Vol target", "Basket vol at 1x (%)", basket_vol * 100)
            self.plot("Vol target", "Expected account vol (%)", basket_vol * self.gross * size * 100)
        self.plot("Positions", "Gross leverage",
                  self.portfolio.total_absolute_holdings_cost / max(self.portfolio.total_portfolio_value, 1))

    def market_allows(self):
        """Market filter at the rebalance: breadth decides whether to trade this week."""
        if self.mkt_mode in ("breadth", "both"):
            share, n_q, n = self.breadth()
            self.plot("Breadth", "Share qualifying", share)
            was_off = self.risk_off
            self.risk_off = n > 0 and share <= self.breadth_min
            if self.risk_off:
                return self.stay_out(f"BREADTH FILTER: only {n_q} of {n} stocks qualify ({share:.0%})")
            if was_off and self.idx_sma.is_ready:                    # restart the fresh-cross check from today
                self.idx_above = self.securities[self.idx].price >= self.idx_sma.current.value
        elif self.mkt_mode == "sma" and self.idx_sma.is_ready:
            self.risk_off = self.securities[self.idx].price < self.idx_sma.current.value
            if self.risk_off:
                return self.stay_out(f"MARKET FILTER: {INDEX_ETF} below its SMA")
        return True

    def stay_out(self, tag):
        if self.portfolio.invested:
            self.liquidate(tag=tag)
        self.plot("Positions", "Holdings", 0)
        return False

    # ───────── qualification, TEO, ranking, breadth ─────────
    def qualifies(self, sd, price):
        slow = sd.slow.current.value
        if not (sd.fast.current.value > slow and price > slow):
            return False
        moms = sd.mom_values()
        if self.mom_positive == "blend":
            return sum(moms) / len(moms) > 0
        if self.mom_positive == "all":
            return min(moms) > 0
        return True

    def candidates(self):
        """[(symbol, momentum values)] for every stock that qualifies now."""
        out = []
        for sym, sd in self.data.items():
            sec = self.securities[sym]
            if sd.ready and sec.has_data and sec.price > 0 and self.qualifies(sd, sec.price):
                out.append((sym, sd.mom_values()))
        return out

    def teo_filter(self, cands):
        """(kept candidates, [(symbol, TEO, stage)] removed). Exhausting stocks are removed; no TEO data -> kept."""
        if not self.teo_on or not cands:
            return cands, []
        closes = self.daily_closes([sym for sym, _ in cands])
        kept, out = [], []
        for sym, moms in cands:
            teo, stage = self.teo_now(closes, sym)
            if stage == EXHAUSTING:
                out.append((sym, teo, stage))
            else:
                kept.append((sym, moms))
        return kept, out

    def teo_now(self, closes, sym):
        """(TEO today, stage) from daily closes, or (None, None) without enough data."""
        if closes is None or sym not in closes.columns:
            return None, None
        c = closes[sym].dropna()
        if len(c) < self.teo_len + self.teo_slope + 2:
            return None, None
        path = c.diff().abs().rolling(self.teo_len).sum()
        er = ((c - c.shift(self.teo_len)) / path.where(path > 0)).fillna(0.0).iloc[self.teo_len:]
        teo = er.ewm(span=self.teo_smooth, adjust=False).mean() if self.teo_smooth > 1 else er
        if len(teo) <= self.teo_slope:
            return None, None
        v = float(teo.iloc[-1])
        slope = (1 if v > 0 else -1 if v < 0 else 0) * (v - float(teo.iloc[-1 - self.teo_slope]))
        return v, teo_stage(v, slope, self.teo_flat, self.teo_deep, self.teo_active, self.teo_dead)

    def ranked(self, cands):
        """Symbols best first: average rank on each lookback (rank) or average return (return)."""
        if self.mom_method == "rank" and cands:
            score = {sym: 0.0 for sym, _ in cands}
            for k in range(len(self.mom_lens)):
                for r, (sym, _) in enumerate(sorted(cands, key=lambda x: x[1][k], reverse=True)):
                    score[sym] -= (r + 1) / len(self.mom_lens)       # higher score = better
            scored = [(score[sym], sym) for sym, _ in cands]
        else:
            scored = [(sum(v) / len(v), sym) for sym, v in cands]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [sym for _, sym in scored]

    def breadth(self):
        """(share, qualifying, counted): stocks with ready indicators and a price that qualify now."""
        n = n_q = 0
        for sym, sd in self.data.items():
            px = self.securities[sym].price
            if sd.ready and px > 0:
                n += 1
                n_q += self.qualifies(sd, px)
        return (n_q / n if n else 0.0), n_q, n

    # ───────── vol target ─────────
    def daily_closes(self, syms):
        """Completed daily closes for syms (columns), enough for the vol target and the TEO warm-up."""
        if not syms:
            return None
        n = max(max(self.vol_lens or [0]) + 1, self.teo_len + self.teo_slope + 10 * self.teo_smooth + 60)
        try:
            return self.history(syms, n, Resolution.DAILY)["close"].unstack(level=0)
        except Exception:  # noqa: BLE001
            return None

    def target_size(self, top, closes=None):
        """(share of the account to deploy 0-1, basket vol at 1x or None)."""
        if self.vol_target <= 0 or not top or not self.vol_lens:
            return 1.0, None
        vol = self.basket_vol(closes.tail(max(self.vol_lens) + 1) if closes is not None else None)
        if vol is None:
            return 1.0, None                                         # no history: full size
        return min(1.0, max(self.vol_min_size, self.vol_target / (self.gross * vol))), vol

    def basket_vol(self, closes):
        """Annualized vol of the equal-weight basket at 1x (empty slots = cash), the higher of the vol_lookbacks."""
        if closes is None or closes.empty or len(closes) < 11:
            return None
        rets = closes.pct_change().iloc[1:].fillna(0.0)              # missing day = flat
        w = 1.0 / self.top_n
        vols = [float((rets.tail(n) * w).sum(axis=1).std(ddof=1)) * (252 ** 0.5)
                for n in self.vol_lens if len(rets.tail(n)) >= max(10, n // 2)]
        vol = max(vols) if vols else 0.0
        return vol if vol > 0 else None

    # ───────── Kalshi carry: interest and funding, once a day at 4:00 PM ET ─────────
    def daily_carry(self):
        today = self.time.date()
        if self.is_warming_up or self.last_carry == today:
            return
        days = max((today - self.last_carry).days, 1) if self.last_carry else 1   # weekends paid on the next day
        self.last_carry = today
        if self.int_mode != "off":
            self.pay_interest(days)
        if self.fund_mode != "off":
            self.pay_funding(days)

    def risk_free(self):
        try:
            return float(self.risk_free_interest_rate_model.get_interest_rate(self.time))
        except Exception:  # noqa: BLE001
            return 0.04

    def positions(self):
        """Holdings of the traded stocks (not SPY / DIA)."""
        return [h for sym, h in self.portfolio.items() if h.invested and sym not in (self.spy, self.idx)]

    def pay_interest(self, days):
        """Interest Kalshi pays on the margin account (all equity)."""
        equity = max(self.portfolio.total_portfolio_value, 0.0)
        grow = lambda bal, apy: bal * ((1 + apy) ** (days / 365.0) - 1) if bal > 0 else 0.0
        if self.int_mode in ("promo", "promo_rates"):
            if self.int_mode == "promo":
                cash_apy, pos_apy = self.int_cash, self.int_pos
            else:
                cash_apy = max(self.risk_free() - self.int_haircut, 0.0)
                pos_apy = cash_apy + (self.int_pos - self.int_cash)
            if self.gross < self.promo_min_lev:
                pos_apy = cash_apy                                   # below 1.5x: no promo rate
            gross_value = sum(abs(h.holdings_value) for h in self.positions())
            backing = min(gross_value / max(self.gross, 1.0), equity)
            interest = grow(backing, pos_apy) + grow(equity - backing, cash_apy)
        else:
            apy = self.int_apy if self.int_mode == "fixed" else max(self.risk_free() - self.int_haircut, 0.0)
            interest = grow(equity, apy)
        if interest:
            self.portfolio.set_cash(self.portfolio.cash + interest)
            self.int_total += interest
            self.plot("Kalshi funding", "Cumulative interest earned ($)", self.int_total)

    def pay_funding(self, days):
        """Perp funding on the position value: longs pay a positive rate, shorts receive it."""
        annual = self.fund_annual if self.fund_mode == "fixed" else self.risk_free() + self.fund_spread
        rate = annual / 365.0 * days                                 # per payment
        if self.fund_cap > 0:
            rate = max(-self.fund_cap, min(self.fund_cap, rate))
        if self.fund_floor > 0 and abs(rate) < self.fund_floor:
            rate = 0.0
        if rate == 0:
            return
        payment = sum(h.holdings_value * rate for h in self.positions())
        if payment:
            self.portfolio.set_cash(self.portfolio.cash - payment)
            self.fund_total += payment
            self.plot("Kalshi funding", "Cumulative funding paid ($)", self.fund_total)
            self.plot("Kalshi funding", "Annual rate (%)", rate / days * 365 * 100)
