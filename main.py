"""Deep-dip bounce bot for the make.fun cbBTC bars-replay race
(agent-fun/bars-replay/v1).

Strategy (long only, at most one position, paper USDC) — a fresh design,
no RSI, no divergence, no moving averages:
  * Watch each 1-minute bar of CBBTC. A buy is considered only when the
    latest close sits at least DIP_BPS below the trailing HIGH_WIN-bar
    high of bar highs — a sharp one-hour washout, not a slow grind — AND
    the latest bar bounced (its close is above the previous close), so
    the fall is already exhausting instead of still accelerating.
  * Entries are all-in (SIZE_PCT of cash). At most one round trip is
    open at a time; a COOLDOWN of bars follows every exit.
  * While holding, sell everything when the close has fallen SL_BPS
    below the entry bar's close (the bounce failed and the washout is
    resuming), or when TIME_STOP bars have passed since the entry bar
    (the bounce is given a fixed window to play out, then banked).
  * Every order carries max_slippage_bps 10000 (the lab measurement the
    bot was tuned on assumed fills are never refused for slippage).

Why this shape: on the kit's dev window (a flat, range-bound day,
+53 bps open to close, with one sharp washout to ~$63,874 and a
recovery to ~$65,461) every trend, breakout, regime and short-horizon
mean-reversion family tested lost money after the harness's fees,
slippage and price impact — the only configurations that made money
were the ones that waited for a rare, deep, already-bouncing dip and
then held it for hours. The chosen parameters buy exactly one such
dip on the dev window (bars 564 -> 984) and sit out the rest.

Tuned on the kit's dev window with a fill-faithful simulator (see
README): the parameters sit mid-plateau — dip 200-220 bps, time stop
300-420 bars and stop 60-90 bps all score +$8 to +$15 on dev — and the
same configuration is net-positive across eight additional real
24-hour BTC windows (a -13.7% crash week): +$31 total, worst window
-$9.49. Looser dip thresholds and two-tier variants were tested and
rejected: they catch falling knives on crash days and lose money
there, while the deep threshold never lost more than ~$13 on any
tested window. Verified on the shipped bot itself (in-process and
twice over stdio, byte-identical action logs): dev net PnL +$14.65,
2 trades, 1 round trip, max drawdown 0.62%.

Deterministic: no wall clock, no randomness, no environment reads;
the only inputs are the hello and the batches themselves. All
arithmetic is integer, identical every run.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars  # noqa: E402

SYMBOL = "CBBTC"

PARAMS = {
    "high_win": 60,      # trailing window (bars) for the reference high
    "dip_bps": 220,      # close must be >= this far below the trailing high
    "sl_bps": 60,        # stop: close this far below the entry close
    "time_stop": 420,    # max bars to hold a position (7 hours)
    "cooldown": 15,      # bars to wait after an exit before re-entering
    "size_pct": 100,     # percent of cash per entry
    "buy_slip_bps": 10000,   # max_slippage_bps on buys (lab-faithful: never refuses)
    "sell_slip_bps": 10000,  # max_slippage_bps on sells
    "min_bars": 60,      # history needed before the first entry
}


class Bot:
    def __init__(self, hello):
        self.p = dict(PARAMS)
        sym = hello["symbols"][0]
        self.qty_scale = sym["qty_scale"]
        self.price_scale = sym["price_scale"]
        self.quote_scale = hello["capital"]["quote_scale"]
        self.min_notional = int(hello["fees"]["min_notional_quote_units"])
        self.closes = []          # close of every bar seen, in order
        self.highs = []           # high of every bar seen, in order
        self.entry_price = 0      # close of the bar that decided the open buy
        self.entry_seq = -1       # bar index that decided the open buy
        self.last_exit = -10 ** 9  # bar index of the last sell decision
        self.pending = None       # "buy" / "sell" while an order awaits its fill
        self.order_n = 0

    def _next_id(self, tag):
        self.order_n += 1
        return "%s%d" % (tag, self.order_n)

    def on_batch(self, state, batch):
        for bar in batch["bars"]:
            if bar["symbol"] == SYMBOL:
                self.closes.append(int(bar["c"]))
                self.highs.append(int(bar["h"]))
        # Fold the fills of the orders decided on the previous batch: any
        # resolution (filled or rejected) clears the pending flag. A
        # rejected buy leaves no position, so the entry logic below may
        # try again on a later bar; a filled buy leaves the position the
        # exit logic watches. The cooldown clock starts at the sell
        # decision (set in on_batch below), not at its fill, matching
        # the lab the parameters were measured on.
        for fill in batch["fills"]:
            if self.pending == "buy" and fill["side"] == "buy":
                self.pending = None
            elif self.pending == "sell" and fill["side"] == "sell":
                self.pending = None
        if state.warming_up or not self.closes:
            return None
        i = len(self.closes) - 1
        close = self.closes[i]
        holding = state.positions.get(SYMBOL)

        if holding is not None and self.pending is None:
            # Exit: the bounce failed (close back below the entry close
            # by SL_BPS) or the holding window ran out.
            gain = (close - self.entry_price) * 10000 // self.entry_price
            if gain <= -self.p["sl_bps"] or i - self.entry_seq >= self.p["time_stop"]:
                self.pending = "sell"
                self.last_exit = i
                return [{
                    "side": "sell",
                    "client_id": self._next_id("s"),
                    "symbol": SYMBOL,
                    "qty": holding.qty,
                    "max_slippage_bps": self.p["sell_slip_bps"],
                }]
            return None

        if holding is not None or self.pending is not None:
            return None
        if i < self.p["min_bars"] or i - self.last_exit < self.p["cooldown"]:
            return None
        # Entry: a deep dip off the trailing 60-bar high, with a bounce
        # already printing (this close above the previous close).
        win = self.highs[max(0, i - self.p["high_win"]):i]
        if not win:
            return None
        hi = max(win)
        if close * 10000 > hi * (10000 - self.p["dip_bps"]):
            return None  # not dipped far enough below the trailing high
        if close <= self.closes[i - 1]:
            return None  # no bounce yet — the fall may still be accelerating
        notional = state.cash * self.p["size_pct"] // 100
        room = state.weight_room(SYMBOL)
        if room < notional:
            notional = room
        if notional < self.min_notional:
            return None
        self.pending = "buy"
        self.entry_price = close
        self.entry_seq = i
        return [{
            "side": "buy",
            "client_id": self._next_id("b"),
            "symbol": SYMBOL,
            "notional_quote_units": notional,
            "max_slippage_bps": self.p["buy_slip_bps"],
        }]


def main():
    holder = {}

    def on_batch(state, batch):
        bot = holder.get("bot")
        if bot is None:
            bot = holder["bot"] = Bot(state.hello)
        return bot.on_batch(state, batch)

    makefun_bars.run(on_batch)


if __name__ == "__main__":
    main()
