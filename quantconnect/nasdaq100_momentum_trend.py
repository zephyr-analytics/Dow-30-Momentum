# region imports
from AlgorithmImports import *
# endregion

# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
#  NASDAQ-100 TOP 30 (QQQ) - MOMENTUM + TREND + VOL TARGET            QuantConnect / LEAN, Python
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
#  Universe   The top `index_top` (30) QQQ holdings by fund weight, point-in-time, re-ranked every
#             `index_refresh` (month). A stock that drops out is sold ("Left the top 30").
#             `exclude` (GOOG) is dropped before ranking so Alphabet counts once (GOOG is kept before its
#             April 3, 2014 split, when it was Alphabet's only share class).
#             QQQ constituent data starts in June 2009. universe=auto uses a proxy before that - the 30
#             largest Nasdaq-listed non-financials by market cap - then switches to the real list.
#             universe=etf: real list only (nothing trades before mid-2009). universe=proxy: proxy only.
#  Data       Hourly bars. Trend, momentum and vol use DAILY bars.
#  Qualify    10-day SMA > 200-day SMA, price > 200-day SMA, and momentum positive
#             (mom_positive: blend = average of the lookback returns > 0, all = every lookback > 0, off).
#  Select     Top `top` (10) qualifiers by momentum over the `momentum` lookbacks (21, 63, 126 days):
#             mom_method=rank averages each stock's rank on each lookback; return averages the returns.
#             Fewer than 10 qualifiers -> the empty slots stay in cash.
#  Size       Each pick gets size x leverage / 10 of the account. Leverage is fixed (`leverage`).
#             Vol target (vol_target > 0) sets SIZE, the share of the account deployed (the rest is idle cash):
#               size = vol_target / (leverage x basket vol), capped at 100%, floored at vol_min_size.
#             Basket vol = realized vol of the exact basket being bought (equal weights, at 1x), the HIGHER
#             of the `vol_lookbacks` (21 / 63 days) - quick to cut when vol jumps, slow to add back.
#             vol_target=0 -> always 100% deployed.
#  Rebalance  Weekly, 15 minutes after Monday's open (Tuesday after a holiday); rebalance=daily for every day.
#  Stop       Every hourly bar: a holding that closes below its 200-day SMA is sold (cash until the rebalance).
#  Market     Kill switch; sells everything. filter_check=hourly (default): checked every hourly bar;
#  filter     close: on the 4:00 PM bar only, the sell fills at the next open. (The stop above is always hourly.)
#               breadth = share of the 30 that qualify. market_filter=both (default) trips when breadth is at or
#               below `breadth_min` (40%) OR QQQ makes a FRESH close below its `market_sma` (200-day SMA).
#               Trading resumes at a rebalance once breadth is above breadth_min, even if QQQ is still below.
#             market_filter=breadth: breadth only.  sma: out while QQQ is below its SMA.  off: no filter.
#  Kalshi     kalshi=on models Kalshi stock perps:
#             - Fee: kalshi_fee (0.02%) of the position value on every open and close.
#             - Funding, once a day at 4:00 PM ET on the position value (longs pay a positive rate). No market
#               history exists, so it is modeled as carry. funding_mode=rates: risk-free rate + funding_spread;
#               fixed: funding_annual; off. Optional funding_cap / funding_floor per payment (0 = off).
#               Prices are dividend-adjusted, so dividends are not subtracted again. No margin interest is
#               charged - with a perp the leverage cost IS the funding.
#             - Interest Kalshi pays on the margin account, daily on the end-of-day balance:
#               promo (default): interest_pos_apy (7%) on margin backing positions (position value / leverage)
#                 when leverage >= promo_min_lev (1.5x), interest_cash_apy (3.5%) on the rest.
#               promo_rates: same split, tracking history: cash = risk-free - interest_haircut (>= 0),
#                 positions = that + (7% - 3.5%).
#               rates: risk-free - interest_haircut on everything.  fixed: interest_apy.  off.
#               The 7% is a promotion with capacity caps and only covers eligible stocks/ETFs.
#
#  Parameters (QuantConnect "Parameters" panel; defaults shown)
#    fast=10  slow=200  momentum=21,63,126  mom_method=rank|return  mom_positive=blend|all|off  top=10
#    leverage=2.0  start_year=2005  rebalance=weekly|daily
#    index_top=30  index_refresh=month|week|day  exclude=GOOG (comma list; empty = none)  universe=auto|etf|proxy
#    vol_target=0.35 (0 = off)  vol_lookbacks=21,63  vol_min_size=0
#    market_filter=both|breadth|sma|off  breadth_min=0.40  filter_check=hourly|close  market_sma=200
#    kalshi=on|off  kalshi_fee=0.0002  funding_mode=rates|fixed|off  funding_spread=0  funding_annual=0.05
#    funding_cap=0  funding_floor=0  interest_mode=promo|promo_rates|rates|fixed|off  interest_pos_apy=0.07
#    interest_cash_apy=0.035  promo_min_lev=1.5  interest_haircut=0.005  interest_apy=0.0325
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

INDEX_ETF = "QQQ"


def ints(text):
    """'21,63,126' -> [21, 63, 126]"""
    return [int(x) for x in str(text).split(",") if x.strip()]


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


class QQQTop30MomentumTrendVol(QCAlgorithm):

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
        # universe
        self.universe_mode = p("universe", "auto")                   # "auto", "etf" or "proxy"
        self.index_top = int(p("index_top", 30))
        self.index_refresh = p("index_refresh", "month")             # "month", "week" or "day"
        self.exclude = {x.strip().upper() for x in str(p("exclude", "GOOG")).split(",") if x.strip()}
        self.etf_live = False                                        # real QQQ constituent data has arrived
        self.etf_key = self.proxy_key = None                         # refresh period of the last ranking
        # sizing
        self.gross = float(p("leverage", 2.0))
        self.vol_target = float(p("vol_target", 0.35))               # annual account vol; 0 = off
        self.vol_lens = ints(p("vol_lookbacks", "21,63"))
        self.vol_min_size = float(p("vol_min_size", 0.0))
        # market filter
        self.mkt_mode = p("market_filter", "both")                   # "both", "breadth", "sma" or "off"
        if self.mkt_mode == "on":                                    # old name for "sma"
            self.mkt_mode = "sma"
        self.breadth_min = float(p("breadth_min", 0.40))             # trade only while MORE than this qualify
        self.filter_check = p("filter_check", "hourly")              # "hourly" or "close" (4:00 PM bar)
        self.mkt_len = int(p("market_sma", self.slow_len))
        self.risk_off = False
        self.idx_above = None                                        # last QQQ close vs its SMA ("both")
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

        self.set_start_date(int(p("start_year", 2005)), 1, 1)
        self.set_cash(10_000)
        self.set_brokerage_model(BrokerageName.INTERACTIVE_BROKERS_BROKERAGE, AccountType.MARGIN)
        self.settings.free_portfolio_value_percentage = 0.02         # cash buffer for fees and slippage

        # each security needs a margin allowance above the gross target or orders are rejected
        self.sec_leverage = max(self.gross + 1.0, 2.0)
        self.universe_settings.resolution = Resolution.HOUR
        self.universe_settings.leverage = self.sec_leverage

        self.spy = self.add_equity("SPY", Resolution.HOUR).symbol     # market clock for scheduling
        self.idx = self.add_equity(INDEX_ETF, Resolution.HOUR).symbol
        self.set_benchmark(INDEX_ETF)
        self.idx_sma = self.sma(self.idx, self.mkt_len, Resolution.DAILY)
        self.warm_up_indicator(self.idx, self.idx_sma, Resolution.DAILY)

        self.data = {}
        if self.universe_mode in ("auto", "etf"):
            self.add_universe(self.universe.etf(INDEX_ETF, self.universe_settings, self.etf_filter))
        if self.universe_mode in ("auto", "proxy"):
            self.add_universe(self.proxy_filter)

        if self.kalshi and (self.fund_mode != "off" or self.int_mode != "off"):
            self.schedule.on(self.date_rules.every_day(self.spy), self.time_rules.at(16, 0), self.daily_carry)
        days = self.date_rules.week_start(self.spy) if self.rebal_mode == "weekly" else self.date_rules.every_day(self.spy)
        self.schedule.on(days, self.time_rules.after_market_open(self.spy, 15), self.rebalance)

    # ───────── universe: top 30 of QQQ ─────────
    def refresh_period(self):
        t = self.time
        if self.index_refresh == "day":
            return t.date()
        if self.index_refresh == "week":
            return tuple(t.isocalendar()[:2])
        return (t.year, t.month)

    def excluded(self, symbol):
        ticker = symbol.value.upper()
        if ticker == "GOOG" and self.time < datetime(2014, 4, 3):
            return False                                             # GOOG was Alphabet's only class then
        return ticker in self.exclude

    def etf_filter(self, constituents):
        """Real QQQ holdings: the index_top largest by fund weight, re-ranked each refresh period."""
        constituents = [c for c in constituents if c.symbol is not None and not self.excluded(c.symbol)]
        if not constituents:
            return Universe.UNCHANGED if self.etf_live else []
        key = self.refresh_period()
        if self.etf_live and key == self.etf_key:
            return Universe.UNCHANGED
        if not self.etf_live:
            self.etf_live = True
            self.debug(f"{self.time:%Y-%m-%d}: QQQ constituent data available - switching from proxy to real top {self.index_top}")
        self.etf_key = key
        ranked = sorted(constituents, key=lambda c: c.weight or 0.0, reverse=True)
        return [c.symbol for c in ranked[: self.index_top]]

    def proxy_filter(self, fundamental):
        """Before mid-2009: the index_top largest Nasdaq-listed non-financials by market cap."""
        if self.universe_mode == "auto" and self.etf_live:
            return []                                                # the real list has taken over
        key = self.refresh_period()
        if key == self.proxy_key:
            return Universe.UNCHANGED
        picks = [f for f in fundamental
                 if f.has_fundamental_data and f.price >= 1 and f.market_cap and not self.excluded(f.symbol)
                 and f.company_reference.primary_exchange_id == "NAS"
                 and f.asset_classification.morningstar_sector_code != MorningstarSectorCode.FINANCIAL_SERVICES]
        if len(picks) < self.index_top:                              # bad data day: try again tomorrow
            return Universe.UNCHANGED
        picks.sort(key=lambda f: f.market_cap, reverse=True)
        self.proxy_key = key
        return [f.symbol for f in picks[: self.index_top]]

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
                self.liquidate(sym, tag=f"Left the top {self.index_top}")
            sd = self.data.pop(sym, None)
            if sd:
                for ind in sd.indicators():
                    self.deregister_indicator(ind)

    # ───────── market filter, then the hourly 200-day SMA stop ─────────
    def on_data(self, slice: Slice):
        if self.is_warming_up:
            return
        bar = slice.bars.get(self.idx)
        at_close = self.time.hour == 16 and self.time.minute == 0
        if bar is not None and (self.filter_check != "close" or at_close) and self.market_trips(bar):
            return
        for sym, sd in self.data.items():
            bar = slice.bars.get(sym)
            if bar is not None and self.portfolio[sym].invested and sd.slow.is_ready \
                    and bar.close < sd.slow.current.value:
                self.liquidate(sym, tag=f"Hourly close {bar.close:.2f} below {self.slow_len}-day SMA {sd.slow.current.value:.2f}")

    def market_trips(self, bar):
        """Hourly kill switch. Sells everything and returns True if it trips."""
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
        top = self.ranked(self.candidates())[: self.top_n]
        size, basket_vol = self.target_size(top)
        weight = size * self.gross / self.top_n
        tag = ("Weekly" if self.rebal_mode == "weekly" else "Daily") + " rebalance"
        if self.vol_target > 0:
            tag += f" (size {size:.0%}, basket vol {basket_vol:.0%})" if basket_vol else f" (size {size:.0%}, no vol data)"
        self.set_holdings([PortfolioTarget(sym, weight) for sym in top], liquidate_existing_holdings=True, tag=tag)

        self.plot("Positions", "Holdings", len(top))
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
            self.risk_off = n > 0 and share <= self.breadth_min
            if self.risk_off:
                return self.stay_out(f"BREADTH FILTER: only {n_q} of {n} stocks qualify ({share:.0%})")
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

    # ───────── qualification, ranking, breadth ─────────
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
    def target_size(self, top):
        """(share of the account to deploy 0-1, basket vol at 1x or None)."""
        if self.vol_target <= 0 or not top or not self.vol_lens:
            return 1.0, None
        try:
            closes = self.history(top, max(self.vol_lens) + 1, Resolution.DAILY)["close"].unstack(level=0)
        except Exception:  # noqa: BLE001
            closes = None
        vol = self.basket_vol(closes)
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
        """Holdings of the traded stocks (not SPY / QQQ)."""
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
            backing = min(gross_value / max(self.gross, 1e-9), equity)
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
