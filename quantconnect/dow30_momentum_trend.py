# region imports
from AlgorithmImports import *
# endregion

# DOW 30 MOMENTUM + TREND (QuantConnect / LEAN, Python)
#
#  Data      : hourly bars. Trend and momentum are measured on DAILY bars built from the hourly data.
#  Trend     : a stock qualifies while its 10-day SMA is above its 200-day SMA AND price is above the 200-day SMA.
#  Selection : of the qualifying stocks, hold the top 10 by momentum = a blend of the 21, 63 and 126-day returns
#              (mom_method=rank (default): average of each stock's rank on each; mom_method=return: average of the returns).
#              Momentum must be positive: mom_positive=blend (average of the returns > 0, default),
#              all (every lookback > 0) or off.
#  Sizing    : 2.5x gross leverage split evenly -> each holding gets 2.5 / 10 = 25% of equity.
#              With fewer than 10 qualifiers, the unused slots stay in cash (no doubling up).
#  Rebalance : once a week, 15 minutes after Monday's open (Tuesday if Monday is a market holiday).
#              Set rebalance=daily to go back to every day. A stock stopped out mid-week stays in cash until then.
#  Kill switches (stop trading):
#   1. Market filter (market_filter=breadth): if breadth_min (40%) OR FEWER of the Dow stocks qualify (10 SMA above
#      the slow SMA, price above it, momentum positive), sell EVERYTHING and buy nothing. Checked every hourly
#      candle (trips) and at each rebalance (resumes once MORE than 40% qualify again). breadth_min=0.40.
#      market_filter=sma -> the old rule (DIA below its slow SMA); market_filter=off -> no filter.
#      market_filter=both (default) -> STOP if EITHER trips on an hourly candle: breadth at or below breadth_min, OR
#      DIA crossing below its SMA (a fresh cross). RESUME at a rebalance when breadth is above breadth_min - even if
#      DIA is still below its SMA. (So the SMA can stop trading, but breadth decides when to start back up.)
#   2. Drawdown breaker (dd_stop=0.25 etc., default 0 = off): if equity falls that far from its peak,
#      sell everything and pause for dd_pause_weeks weeks (default 4); the peak then resets.
#  KALSHI PERP COSTS (kalshi=on, default):
#   Funding   : charged ONCE A DAY at 4:00 PM ET on the full position value (Kalshi stock perps). Positive rate:
#               longs pay, shorts receive. Kalshi's rate is market-driven (perp vs spot premium) and has no history
#               before launch, so it is modeled as carry: funding_mode=rates -> the US risk-free rate on that date
#               (LEAN's rate history) + funding_spread; fixed -> funding_annual every day; off -> none.
#               Prices are dividend-adjusted (you get dividends in price), so dividends are NOT subtracted again.
#               Optional Kalshi-style rules: funding_cap (max |rate| per payment, crypto perps use 2%) and
#               funding_floor (|rate| per payment below this -> 0; crypto perps use 0.01%). Both 0 = off.
#   Fees      : kalshi_fee (default 0.02%) of the full position value, charged on every open and close.
#   No margin interest is charged - with a perp the leverage cost IS the funding.
#   Interest  : Kalshi pays interest on the perps margin account, on the end-of-day balance (paid monthly).
#               With perps the whole account equity stays as margin; the part BACKING positions and the idle part
#               can earn different rates. Kalshi promo (Oct 29, 2026): margin backing eligible positions with at
#               least 1.5x leverage earns 7%; cash not backing a position earns 3.5% ($250 average balance minimum).
#               Margin backing positions = position value / leverage.
#     interest_mode=promo (default): interest_pos_apy (0.07) on margin backing positions if leverage >= promo_min_lev
#                   (1.5), otherwise interest_cash_apy; interest_cash_apy (0.035) on idle cash. Flat every day.
#     promo_rates : same split, but tracks history: idle cash = risk-free - interest_haircut (>= 0), positions =
#                   that + (interest_pos_apy - interest_cash_apy). Fairer for 2011-2021 when rates were near 0.
#     rates       : one rate on everything = risk-free - interest_haircut.   fixed: interest_apy on everything.   off.
#   NOTE: the 7% is a promotion with capacity caps and can change any time; only eligible stocks/ETFs qualify.
#  Stop      : every hourly candle - if a holding closes below its 200-day SMA, it is closed right away
#              (fills at the next available price; after the last candle of the day, at the next open).
#
# Parameters (QuantConnect "Parameters" panel, so you can optimize them):
#   fast=10  slow=200  momentum=21,63,126  mom_method=rank|return  top=10  leverage=2.5  universe=etf|static  start_year=2010  rebalance=weekly|daily
#   market_filter=both|breadth|sma|off  breadth_min=0.40  market_sma=200  dd_stop=0 (off) or e.g. 0.25  dd_pause_weeks=4
#   kalshi=on|off  kalshi_fee=0.0002  funding_mode=rates|fixed|off  funding_spread=0  funding_annual=0.05
#   funding_cap=0  funding_floor=0  interest_mode=promo|promo_rates|rates|fixed|off  interest_pos_apy=0.07
#   interest_cash_apy=0.035  promo_min_lev=1.5  interest_haircut=0.005  interest_apy=0.0325

# Current Dow 30 (after Alphabet replaced Verizon on June 29, 2026). Only used when universe=static.
DOW30 = ["AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
         "GOOGL", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM",
         "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "WMT"]


class PercentOfNotionalFeeModel(FeeModel):
    """Kalshi-style fee: a % of the full position value on every open and close."""
    def __init__(self, rate):
        super().__init__()
        self.rate = rate

    def get_order_fee(self, parameters):
        sec = parameters.security
        value = abs(parameters.order.absolute_quantity) * sec.price * self.rate
        return OrderFee(CashAmount(value, "USD"))


class SymbolData:
    def __init__(self, algo, symbol, fast, slow, mom_lens):
        self.symbol = symbol
        self.fast = algo.sma(symbol, fast, Resolution.DAILY)
        self.slow = algo.sma(symbol, slow, Resolution.DAILY)
        self.moms = [algo.rocp(symbol, n, Resolution.DAILY) for n in mom_lens]
        for ind in self.indicators():
            algo.warm_up_indicator(symbol, ind, Resolution.DAILY)

    @property
    def ready(self):
        return all(ind.is_ready for ind in self.indicators())

    def mom_values(self):
        return [m.current.value for m in self.moms]

    def indicators(self):
        return [self.fast, self.slow] + self.moms


class Dow30MomentumTrend(QCAlgorithm):

    def initialize(self):
        self.fast_len = int(self.get_parameter("fast", 10))
        self.slow_len = int(self.get_parameter("slow", 200))
        self.mom_lens = [int(x) for x in str(self.get_parameter("momentum", "21,63,126")).split(",") if x.strip()]
        self.mom_method = self.get_parameter("mom_method", "rank")   # "return" or "rank"
        self.mom_positive = self.get_parameter("mom_positive", "blend")  # "blend", "all" or "off"
        self.top_n = int(self.get_parameter("top", 10))
        self.gross = float(self.get_parameter("leverage", 2.5))
        self.universe_mode = self.get_parameter("universe", "etf")   # "etf" = point-in-time DIA members, "static" = today's list
        start_year = int(self.get_parameter("start_year", 2010))
        self.rebal_mode = self.get_parameter("rebalance", "weekly")      # "weekly" (Friday) or "daily"
        self.mkt_mode = self.get_parameter("market_filter", "both")      # "both", "breadth", "sma" (DIA vs its SMA) or "off"
        self.dia_above = None          # for "both": DIA trips only on a fresh cross below its SMA
        if self.mkt_mode == "on":
            self.mkt_mode = "sma"
        self.mkt_filter = self.mkt_mode != "off"
        self.breadth_min = float(self.get_parameter("breadth_min", 0.40))  # trade only if MORE than this share qualify
        self.mkt_len = int(self.get_parameter("market_sma", self.slow_len))
        self.dd_stop = float(self.get_parameter("dd_stop", 0))          # 0 = off
        self.dd_pause = int(self.get_parameter("dd_pause_weeks", 4))
        self.kalshi = self.get_parameter("kalshi", "on") == "on"
        self.kalshi_fee = float(self.get_parameter("kalshi_fee", 0.0002))
        self.fund_mode = self.get_parameter("funding_mode", "rates")      # "rates", "fixed" or "off"
        self.fund_spread = float(self.get_parameter("funding_spread", 0.0))
        self.fund_annual = float(self.get_parameter("funding_annual", 0.05))
        self.fund_cap = float(self.get_parameter("funding_cap", 0.0))     # e.g. 0.02 (per payment); 0 = off
        self.fund_floor = float(self.get_parameter("funding_floor", 0.0)) # e.g. 0.0001 (per payment); 0 = off
        self.int_mode = self.get_parameter("interest_mode", "promo")     # "promo", "promo_rates", "rates", "fixed", "off"
        self.int_pos = float(self.get_parameter("interest_pos_apy", 0.07))
        self.int_cash = float(self.get_parameter("interest_cash_apy", 0.035))
        self.promo_min_lev = float(self.get_parameter("promo_min_lev", 1.5))
        self.int_haircut = float(self.get_parameter("interest_haircut", 0.005))
        self.int_apy = float(self.get_parameter("interest_apy", 0.0325))
        self.int_total = 0.0
        self.fund_total = 0.0
        self.fund_last_day = None

        self.set_start_date(start_year, 1, 1)
        self.set_cash(10_000)
        self.set_brokerage_model(BrokerageName.INTERACTIVE_BROKERS_BROKERAGE, AccountType.MARGIN)
        self.settings.free_portfolio_value_percentage = 0.02          # small cash buffer for fees and slippage

        # Each security needs a margin allowance above the 3x target or orders get rejected.
        # NOTE: a real Reg T account allows 2x overnight; 3x overnight needs portfolio margin or futures/perps.
        self.sec_leverage = max(self.gross + 1.0, 2.0)
        self.universe_settings.resolution = Resolution.HOUR
        self.universe_settings.leverage = self.sec_leverage

        self.spy = self.add_equity("SPY", Resolution.HOUR).symbol      # market clock for scheduling
        self.set_benchmark("DIA")
        self.dia = self.add_equity("DIA", Resolution.HOUR).symbol
        self.dia_sma = self.sma(self.dia, self.mkt_len, Resolution.DAILY)
        self.warm_up_indicator(self.dia, self.dia_sma, Resolution.DAILY)
        self.risk_off = False          # market filter tripped
        self.peak = 0.0
        self.paused_until = None       # drawdown breaker pause

        self.data = {}
        if self.universe_mode == "etf":
            self.add_universe(self.universe.etf("DIA", self.universe_settings, self.etf_filter))
        else:
            for t in DOW30:
                self.add_equity(t, Resolution.HOUR, leverage=self.sec_leverage)

        if self.kalshi and (self.fund_mode != "off" or self.int_mode != "off"):
            self.schedule.on(self.date_rules.every_day(self.spy), self.time_rules.at(16, 0), self.daily_carry)

        date_rule = self.date_rules.week_start(self.spy) if self.rebal_mode == "weekly" else self.date_rules.every_day(self.spy)
        self.schedule.on(date_rule,
                         self.time_rules.after_market_open(self.spy, 15),
                         self.rebalance)

    def etf_filter(self, constituents):
        return [c.symbol for c in constituents]

    # ───────── universe changes ─────────
    def on_securities_changed(self, changes):
        for sec in changes.added_securities:
            sym = sec.symbol
            if sym in (self.spy, self.dia) or sym in self.data:
                continue
            sec.set_leverage(self.sec_leverage)
            if self.kalshi:
                sec.set_fee_model(PercentOfNotionalFeeModel(self.kalshi_fee))
            self.data[sym] = SymbolData(self, sym, self.fast_len, self.slow_len, self.mom_lens)
        for sec in changes.removed_securities:
            sym = sec.symbol
            if sym in (self.spy, self.dia):
                continue
            if self.portfolio[sym].invested:
                self.liquidate(sym, tag="Left the Dow")
            sd = self.data.pop(sym, None)
            if sd:
                for ind in sd.indicators():
                    self.deregister_indicator(ind)

    # ───────── hourly stop: close below the 200-day SMA ─────────
    def on_data(self, slice: Slice):
        if self.is_warming_up:
            return

        # kill switch 1: market filter
        dia_bar = slice.bars.get(self.dia)
        if self.mkt_mode == "sma" and dia_bar is not None and self.dia_sma.is_ready and not self.risk_off \
                and dia_bar.close < self.dia_sma.current.value:
            self.risk_off = True
            self.liquidate(tag=f"MARKET FILTER: DIA {dia_bar.close:.2f} below {self.mkt_len}-day SMA {self.dia_sma.current.value:.2f}")
            return
        if self.mkt_mode in ("breadth", "both") and not self.risk_off and dia_bar is not None:
            share, n_q, n = self.breadth()
            if n and share <= self.breadth_min:
                self.risk_off = True
                self.liquidate(tag=f"BREADTH FILTER: only {n_q} of {n} stocks qualify ({share:.0%})")
                return
        if self.mkt_mode == "both" and dia_bar is not None and self.dia_sma.is_ready:
            above = dia_bar.close >= self.dia_sma.current.value
            crossed_below = self.dia_above is True and not above
            self.dia_above = above
            if crossed_below and not self.risk_off:
                self.risk_off = True
                self.liquidate(tag=f"SMA FILTER: DIA {dia_bar.close:.2f} crossed below its {self.mkt_len}-day SMA {self.dia_sma.current.value:.2f}")
                return

        # kill switch 2: drawdown breaker
        if self.dd_stop > 0 and self.paused_until is None:
            eq = self.portfolio.total_portfolio_value
            self.peak = max(self.peak, eq)
            if self.peak > 0 and eq < self.peak * (1 - self.dd_stop):
                self.paused_until = self.time + timedelta(weeks=self.dd_pause)
                self.liquidate(tag=f"DRAWDOWN BREAKER: equity down {(1 - eq / self.peak) * 100:.1f}% from peak - paused until {self.paused_until:%Y-%m-%d}")
                return
        for sym, sd in self.data.items():
            if not self.portfolio[sym].invested or not sd.slow.is_ready:
                continue
            bar = slice.bars.get(sym)
            if bar is None:
                continue
            if bar.close < sd.slow.current.value:
                self.liquidate(sym, tag=f"Hourly close {bar.close:.2f} below {self.slow_len}-day SMA {sd.slow.current.value:.2f}")

    # ───────── weekly rebalance ─────────
    def rebalance(self):
        if self.is_warming_up:
            return
        # market filter: stay out until DIA is back above its SMA
        if self.mkt_mode in ("breadth", "both"):
            share, n_q, n = self.breadth()
            self.plot("Breadth", "Share qualifying", share)
            self.risk_off = n > 0 and share <= self.breadth_min
            if self.risk_off:
                if self.portfolio.invested:
                    self.liquidate(tag=f"BREADTH FILTER: only {n_q} of {n} stocks qualify ({share:.0%})")
                self.plot("Positions", "Holdings", 0)
                return
        elif self.mkt_mode == "sma" and self.dia_sma.is_ready:
            dia_px = self.securities[self.dia].price
            self.risk_off = dia_px < self.dia_sma.current.value
            if self.risk_off:
                if self.portfolio.invested:
                    self.liquidate(tag="MARKET FILTER: DIA below its SMA")
                self.plot("Positions", "Holdings", 0)
                return
        # drawdown breaker: stay out until the pause ends, then reset the peak
        if self.paused_until is not None:
            if self.time < self.paused_until:
                self.plot("Positions", "Holdings", 0)
                return
            self.paused_until = None
            self.peak = self.portfolio.total_portfolio_value
        cands = []
        for sym, sd in self.data.items():
            if not sd.ready:
                continue
            sec = self.securities[sym]
            if not sec.has_data or sec.price <= 0:
                continue
            if self.qualifies(sd, sec.price):
                cands.append((sym, sd.mom_values()))

        if self.mom_method == "rank" and cands:
            # rank each lookback separately (1 = strongest), then average the ranks
            score = {sym: 0.0 for sym, _ in cands}
            for k in range(len(self.mom_lens)):
                order = sorted(cands, key=lambda x: x[1][k], reverse=True)
                for r, (sym, _) in enumerate(order):
                    score[sym] -= (r + 1) / len(self.mom_lens)      # higher score = better
            ranked = [(score[sym], sym) for sym, _ in cands]
        else:
            ranked = [(sum(v) / len(v), sym) for sym, v in cands]   # average of the 21, 63 and 126-day returns
        ranked.sort(key=lambda x: x[0], reverse=True)
        top = [sym for _, sym in ranked[: self.top_n]]

        weight = self.gross / self.top_n
        targets = [PortfolioTarget(sym, weight) for sym in top]
        self.set_holdings(targets, liquidate_existing_holdings=True, tag=("Weekly" if self.rebal_mode == "weekly" else "Daily") + " rebalance")

        self.plot("Positions", "Holdings", len(top))
        self.plot("Positions", "Gross leverage", self.portfolio.total_absolute_holdings_cost / max(self.portfolio.total_portfolio_value, 1))

    # ───────── qualification and breadth ─────────
    def qualifies(self, sd, price):
        slow = sd.slow.current.value
        if not (sd.fast.current.value > slow and price > slow):
            return False
        moms = sd.mom_values()
        if self.mom_positive == "blend" and sum(moms) / len(moms) <= 0:
            return False
        if self.mom_positive == "all" and min(moms) <= 0:
            return False
        return True

    def breadth(self):
        """Share of the index's stocks (with ready indicators and a price) that qualify right now."""
        n = n_q = 0
        for sym, sd in self.data.items():
            if not sd.ready:
                continue
            px = self.securities[sym].price
            if px <= 0:
                continue
            n += 1
            n_q += self.qualifies(sd, px)
        return (n_q / n if n else 0.0), n_q, n

    # ───────── Kalshi funding (once a day at 4:00 PM ET) ─────────
    def risk_free(self):
        try:
            return float(self.risk_free_interest_rate_model.get_interest_rate(self.time))
        except Exception:  # noqa: BLE001
            return 0.04

    def funding_rate_annual(self):
        if self.fund_mode == "fixed":
            return self.fund_annual
        return self.risk_free() + self.fund_spread

    def daily_carry(self):
        """4:00 PM ET: Kalshi funding on positions, then interest on the whole margin account."""
        if self.is_warming_up or self.fund_last_day == self.time.date():
            return
        self.fund_last_day = self.time.date()
        # days since the last payment (weekends/holidays are paid on the next trading day)
        prev = getattr(self, "_fund_prev", None)
        days = max((self.time.date() - prev).days, 1) if prev else 1
        self._fund_prev = self.time.date()

        # interest on margin (all account equity)
        if self.int_mode != "off":
            equity = self.portfolio.total_portfolio_value
            grow = lambda bal, apy: bal * ((1 + apy) ** (days / 365.0) - 1) if bal > 0 else 0.0
            if self.int_mode in ("promo", "promo_rates"):
                if self.int_mode == "promo":
                    cash_apy, pos_apy = self.int_cash, self.int_pos
                else:
                    cash_apy = max(self.risk_free() - self.int_haircut, 0.0)
                    pos_apy = cash_apy + (self.int_pos - self.int_cash)
                if self.gross < self.promo_min_lev:
                    pos_apy = cash_apy                                    # below 1.5x: no promo rate
                gross_value = sum(abs(h.holdings_value) for sym, h in self.portfolio.items()
                                  if h.invested and sym not in (self.spy, self.dia))
                backing = min(gross_value / max(self.gross, 1e-9), max(equity, 0.0))
                interest = grow(backing, pos_apy) + grow(max(equity, 0.0) - backing, cash_apy)
            else:
                apy = self.int_apy if self.int_mode == "fixed" else max(self.risk_free() - self.int_haircut, 0.0)
                interest = grow(equity, apy)
            if interest:
                self.portfolio.set_cash(self.portfolio.cash + interest)
                self.int_total += interest
                self.plot("Kalshi funding", "Cumulative interest earned ($)", self.int_total)

        if self.fund_mode == "off":
            return
        rate = self.funding_rate_annual() / 365.0 * days                 # per payment
        if self.fund_cap > 0:
            rate = max(-self.fund_cap, min(self.fund_cap, rate))
        if self.fund_floor > 0 and abs(rate) < self.fund_floor:
            rate = 0.0
        if rate == 0:
            return
        payment = 0.0
        for sym, h in self.portfolio.items():
            if not h.invested or sym in (self.spy, self.dia):
                continue
            payment += h.holdings_value * rate        # long: positive value -> pays; short: negative -> receives
        if payment:
            self.portfolio.set_cash(self.portfolio.cash - payment)
            self.fund_total += payment
            self.plot("Kalshi funding", "Cumulative funding paid ($)", self.fund_total)
            self.plot("Kalshi funding", "Annual rate (%)", rate / max(days, 1) * 365 * 100)
