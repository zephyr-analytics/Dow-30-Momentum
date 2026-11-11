# region imports
from AlgorithmImports import *
# endregion

# DOW 30 MOMENTUM + TREND (QuantConnect / LEAN, Python)
#  Same code and defaults as the QQQ algo, except: the universe (DIA members / DOW30 list), the market ETF
#  (DIA: SMA filter + benchmark), and NO vol target by default (vol_target=0 -> always 100% deployed at 2.5x).
#
#  Data      : hourly bars. Trend and momentum are measured on DAILY bars built from the hourly data.
#  Trend     : a stock qualifies while its 10-day SMA is above its 200-day SMA AND price is above the 200-day SMA.
#  Selection : of the qualifying stocks, hold the top 10 by momentum = a blend of the 21, 63 and 126-day returns
#              (mom_method=rank (default): average of each stock's rank on each; mom_method=return: average of the returns).
#              Momentum must be positive: mom_positive=blend (average of the returns > 0, default),
#              all (every lookback > 0) or off.
#  Sizing    : leverage L (the "leverage" parameter, fixed) split evenly -> each holding gets L / top of the
#              DEPLOYED part of the account (e.g. 2.5 / 10 = 25%).
#              With fewer than 10 qualifiers, the unused slots stay in cash (no doubling up).
#              VOL TARGET (vol_target > 0, default 0.35 = 35% a year): sets PORTFOLIO SIZE, not leverage.
#              At each rebalance, size = the share of the account put to work (0-100%); the rest sits as idle
#              cash. Leverage on the deployed part stays at L.
#              Expected account vol = size x L x basket vol, where basket vol = realized vol of the exact
#              basket about to be bought (same weights, at 1x) over the last 21 and 63 days, whichever is
#              HIGHER (vol_lookbacks) - quick to shrink when vol jumps, slow to grow back.
#              size = vol_target / (L x basket vol), capped at 100% (never above the normal full size) and
#              floored at vol_min_size (0). Ten correlated names (e.g. all chips) = high basket vol = smaller size.
#              vol_target=0 -> off: always 100% deployed, as before.
#  TEO filter: (teo_filter=on, default) at each rebalance, every qualifying stock is checked with the Trend
#              Efficiency Oscillator on daily bars (same as the TradingView TEO: signed Kaufman efficiency ratio over
#              teo_len days, EMA-smoothed by teo_smooth) and given the indicator's STAGE:
#                slope = sign(TEO) x (TEO now - TEO teo_slope days ago)   (+ = moving away from zero = strengthening)
#                |TEO| < teo_dead                           -> Dormant
#                slope > +teo_flat                          -> Extended if |TEO| >= teo_deep, else Building
#                slope < -teo_flat                          -> Fading if |TEO| < teo_active, else Exhausting
#                otherwise                                  -> Extended if |TEO| >= teo_deep, else Flat
#              TEO is part of SELECTION: at each rebalance, every stock that qualifies (trend + momentum) is checked,
#              and any in an EXHAUSTING stage is removed BEFORE ranking. The top 10 are then picked from
#              the rest, so the next-best stock moves up into the 10. Only if fewer than 10 pass do slots stay in cash.
#              Breadth is NOT affected - it still counts trend + momentum only.
#  Rebalance : once a week, 15 minutes after Monday's open (Tuesday if Monday is a market holiday).
#              Set rebalance=daily to go back to every day. Between rebalances only the index filters can sell.
#  Kill switches (stop trading):
#   1. Market filter (market_filter=breadth): if breadth_min (40%) OR FEWER of the Dow stocks qualify (10 SMA above
#      the slow SMA, price above it, momentum positive), sell EVERYTHING and buy nothing. Checked at the daily
#      close (filter_check=close) and at each rebalance (resumes once MORE than 40% qualify again). breadth_min=0.40.
#      market_filter=sma -> the old rule (DIA below its slow SMA); market_filter=off -> no filter.
#      market_filter=both (default) -> STOP if EITHER trips at the check: breadth at or below breadth_min, OR
#      DIA crossing below its SMA (a fresh cross). RESUME at a rebalance when breadth is above breadth_min - even if
#      DIA is still below its SMA. (So the SMA can stop trading, but breadth decides when to start back up.)
#      filter_check=close (default) -> the kill switches only look at the daily close (4:00 PM bar); the sell fills
#      at the next open. filter_check=hourly -> checked every hourly candle.
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
#  Stop      : none per stock. A holding is only sold at the weekly rebalance (dropped out of the top 10) or when an
#              index kill switch trips (breadth / DIA SMA / drawdown breaker).
#
# Parameters (QuantConnect "Parameters" panel, so you can optimize them):
#   fast=10  slow=200  momentum=21,63,126  mom_method=rank|return  top=10  leverage=2.5  universe=etf|static  start_year=2010  rebalance=weekly|daily
#   vol_target=0.35 (0 = off)  vol_lookbacks=21,63  vol_min_size=0
#   teo_filter=on|off  teo_len=21  teo_smooth=7  teo_slope=6  teo_flat=0.02  teo_deep=0.50  teo_active=0.25  teo_dead=0.10
#   market_filter=both|breadth|sma|off  breadth_min=0.40  filter_check=close|hourly
#   market_sma=200  dd_stop=0 (off) or e.g. 0.25  dd_pause_weeks=4
#   kalshi=on|off  kalshi_fee=0.0002  funding_mode=rates|fixed|off  funding_spread=0  funding_annual=0.05
#   funding_cap=0  funding_floor=0  interest_mode=promo|promo_rates|rates|fixed|off  interest_pos_apy=0.07
#   interest_cash_apy=0.035  promo_min_lev=1.5  interest_haircut=0.005  interest_apy=0.0325

# Current Dow 30 (after Alphabet replaced Verizon on June 29, 2026). Only used when universe=static.
DOW30 = ["AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
         "GOOGL", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM",
         "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "WMT"]


TEO_STAGES = {0: "Dormant", 1: "Building", 2: "Flat", 3: "Extended", 4: "Exhausting", 5: "Fading"}


def teo_stage(teo, slope, flat, deep, active, dead):
    """TEO stage. slope = sign(TEO) x (TEO now - TEO slope-days ago): + = strengthening, - = weakening."""
    mag = abs(teo)
    if mag < dead:
        return 0
    if slope > flat:
        return 3 if mag >= deep else 1
    if slope < -flat:
        return 5 if mag < active else 4
    return 3 if mag >= deep else 2


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
        self.gross = float(self.get_parameter("leverage", 1.0))     # leverage on the deployed part (fixed)
        self.vol_target = float(self.get_parameter("vol_target", 0.0))   # Dow: off (always 100% deployed); > 0 = annual vol target
        self.vol_lens = [int(x) for x in str(self.get_parameter("vol_lookbacks", "21,63")).split(",") if x.strip()]
        self.vol_min_size = float(self.get_parameter("vol_min_size", 0.0))  # smallest share of the account deployed
        self.size = 1.0                # share of the account deployed (set each rebalance)
        self.teo_on = self.get_parameter("teo_filter", "on") == "on"
        self.teo_len = int(self.get_parameter("teo_len", 21))
        self.teo_smooth = int(self.get_parameter("teo_smooth", 7))
        self.teo_slope = int(self.get_parameter("teo_slope", 6))
        self.teo_deep = float(self.get_parameter("teo_deep", 0.50))
        self.teo_active = float(self.get_parameter("teo_active", 0.25))
        self.teo_dead = float(self.get_parameter("teo_dead", 0.10))
        self.teo_flat = float(self.get_parameter("teo_flat", 0.02))
        self.universe_mode = self.get_parameter("universe", "etf")   # "etf" = point-in-time DIA members, "static" = today's list
        start_year = int(self.get_parameter("start_year", 2010))
        self.rebal_mode = self.get_parameter("rebalance", "weekly")      # "weekly" (Friday) or "daily"
        self.mkt_mode = self.get_parameter("market_filter", "both")      # "both", "breadth", "sma" (DIA vs its SMA) or "off"
        self.dia_above = None          # for "both": DIA trips only on a fresh cross below its SMA
        if self.mkt_mode == "on":
            self.mkt_mode = "sma"
        self.mkt_filter = self.mkt_mode != "off"
        self.breadth_min = float(self.get_parameter("breadth_min", 0.40))  # trade only if MORE than this share qualify
        # filter_check=close -> the kill switches look at the DAILY CLOSE only
        self.filter_check = self.get_parameter("filter_check", "close")    # "close" or "hourly"
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
        self.sec_leverage = max(self.gross + 1.0, 3.0)
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

    # ───────── index kill switches (no per-stock stop) ─────────
    def on_data(self, slice: Slice):
        if self.is_warming_up:
            return

        # kill switch 1: market filter (filter_check=close -> only on the 4:00 PM bar, i.e. the daily close;
        # the sell then fills at the next open)
        dia_bar = slice.bars.get(self.dia)
        if self.filter_check == "close" and not (self.time.hour == 16 and self.time.minute == 0):
            dia_bar = None
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
        # no per-stock stop: holdings only change at the weekly rebalance or when an index filter trips

    # ───────── weekly rebalance ─────────
    def rebalance(self):
        if self.is_warming_up:
            return
        # market filter: out while breadth <= breadth_min; resumes when breadth is above it again
        if self.mkt_mode in ("breadth", "both"):
            share, n_q, n = self.breadth()
            self.plot("Breadth", "Share qualifying", share)
            was_off = self.risk_off
            self.risk_off = n > 0 and share <= self.breadth_min
            if self.risk_off:
                if self.portfolio.invested:
                    self.liquidate(tag=f"BREADTH FILTER: only {n_q} of {n} stocks qualify ({share:.0%})")
                self.plot("Positions", "Holdings", 0)
                return
            if was_off and self.dia_sma.is_ready:
                # restart the fresh-cross check from today's position vs the SMA
                self.dia_above = self.securities[self.dia].price >= self.dia_sma.current.value
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

        # TEO filter: drop qualifying stocks whose TEO is Exhausting BEFORE ranking
        teo_out = []
        if self.teo_on and cands:
            closes_all = self.daily_closes([sym for sym, _ in cands])
            passed = []
            for sym, moms in cands:
                teo, stage = self.teo_now(closes_all, sym)
                if stage == 4:                          # Exhausting
                    teo_out.append((sym, teo, stage))
                else:
                    passed.append((sym, moms))          # no TEO data yet -> not filtered
            cands = passed

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

        closes = self.daily_closes(top)
        size, basket_vol = self.target_size(top, closes)
        self.size = size

        weight = size * self.gross / self.top_n
        targets = [PortfolioTarget(sym, weight) for sym in top]
        tag = ("Weekly" if self.rebal_mode == "weekly" else "Daily") + " rebalance"
        if self.vol_target > 0:
            tag += f" (size {size:.0%}, basket vol {basket_vol:.0%})" if basket_vol else f" (size {size:.0%}, no vol data)"
        if self.teo_on:
            tag += f" | TEO removed {len(teo_out)} of {len(teo_out) + len(cands)}"
            if teo_out:
                self.debug(f"{self.time:%Y-%m-%d} TEO removed: " + ", ".join(
                    f"{sym.value} {teo:.2f} {TEO_STAGES[stage]}" for sym, teo, stage in teo_out))
        if targets:
            self.set_holdings(targets, liquidate_existing_holdings=True, tag=tag)
        elif self.portfolio.invested:
            self.liquidate(tag="TEO filter: no qualifying stock passed TEO - all cash")

        self.plot("Positions", "Holdings", len(top))
        self.plot("TEO filter", "Candidates removed", len(teo_out))
        self.plot("Vol target", "Portfolio size (% of account)", size * 100)
        if basket_vol:
            self.plot("Vol target", "Basket vol at 1x (%)", basket_vol * 100)
            self.plot("Vol target", "Expected account vol (%)", basket_vol * self.gross * size * 100)
        self.plot("Positions", "Gross leverage", self.portfolio.total_absolute_holdings_cost / max(self.portfolio.total_portfolio_value, 1))

    # ───────── vol target: portfolio size ─────────
    def daily_closes(self, top):
        """Completed daily closes for the picks: enough bars for the vol target and to warm up TEO."""
        if not top:
            return None
        n = max(max(self.vol_lens or [0]) + 1, self.teo_len + self.teo_slope + 10 * self.teo_smooth + 60)
        try:
            return self.history(top, n, Resolution.DAILY)["close"].unstack(level=0)
        except Exception:  # noqa: BLE001
            return None

    def teo_now(self, closes, sym):
        """TEO today and its stage (0 Dormant 1 Building 2 Flat 3 Extended 4 Exhausting 5 Fading). None if no data."""
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

    def target_size(self, top, closes=None):
        """Share of the account to deploy this week (0-1). Returns (size, basket vol at 1x or None)."""
        if self.vol_target <= 0 or not top or not self.vol_lens:
            return 1.0, None
        if closes is not None:
            closes = closes.tail(max(self.vol_lens) + 1)
        if closes is None or closes.empty or len(closes) < 11:
            return 1.0, None                                # no history: normal full size
        rets = closes.pct_change().iloc[1:].fillna(0.0)    # missing day = treated as flat
        w = 1.0 / self.top_n                                # same weights as the real basket; unused slots = cash
        vols = []
        for n in self.vol_lens:
            r = rets.tail(n)
            if len(r) < max(10, n // 2):
                continue
            port = (r * w).sum(axis=1)                      # daily return of the basket at 1x
            vols.append(float(port.std(ddof=1)) * (252 ** 0.5))
        vol = max(vols) if vols else 0.0
        if vol <= 0:
            return 1.0, None
        size = self.vol_target / (self.gross * vol)
        return min(1.0, max(self.vol_min_size, size)), vol

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
                # margin backing = position value / leverage; with a smaller size the rest of the account is idle
                backing = min(gross_value / max(self.gross, 1.0), max(equity, 0.0))
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
