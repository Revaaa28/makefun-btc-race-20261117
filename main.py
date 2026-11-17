"""Deep-dip bounce bot v2 for the make.fun cbBTC bars-replay race
(agent-fun/bars-replay/v1).

Strategy (long only, at most one position, paper USDC):
  * Watch each 1-minute bar of CBBTC. A buy is considered only when
    the latest close sits at least DIP_BPS below the trailing
    HIGH_WIN-bar high of bar highs — a sharp one-hour washout, not a
    slow grind — AND the latest bar bounced (its close is above the
    previous close), so the fall is already exhausting instead of
    still accelerating, AND Wilder RSI(14) at that bar is at or below
    RSI_MAX, so the washout's momentum is genuinely oversold rather
    than merely falling.
  * Entries are all-in (SIZE_PCT of cash). At most one round trip is
    open at a time; a COOLDOWN of bars follows every exit.
  * While holding, sell everything when the close has fallen SL_BPS
    below the entry bar's close (the bounce failed and the washout is
    resuming), or when TIME_STOP bars have passed since the entry bar
    (the bounce is given a fixed window to play out, then banked).
  * Every order carries max_slippage_bps 10000 (the lab measurement
    the bot was tuned on assumed fills are never refused for
    slippage).

Why the RSI gate (v2, added 2026-11-17): measured on the official
fill model across ten 24-hour windows (this kit's dev window, the
previous race kit's real pool tape of Nov 14-15, and eight Binance
days, Nov 9-16), v1 netted +$25.17 in total with a worst window of
-$20.29. The RSI gate leaves the dev trade untouched (dev still
+$14.65) and raises the ten-window total to +$71.39 with the worst
window at -$9.49: on the Nov 14 pool tape, v1's two losing entries
were taken while RSI was still above 35 — falling, but not yet
washed out — and the gate filters exactly those, turning that
window from -$20.29 into +$21.88. Neighbouring thresholds are
weaker (RSI 32: +$45.35; RSI 38: +$29.22), and combining the gate
with a wider stop destroys the gain (+$28.09), so v2 changes this
one thing only. The sample is small (8 round trips); no edge is
proven — this is the best-measured configuration, stated plainly.

Deterministic: no wall clock, no randomness, no environment reads;
the only inputs are the hello and the batches themselves. Entry and
exit arithmetic is integer; the RSI uses float Wilder smoothing,
identical on every run given the same bars.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars  # noqa: E402

SYMBOL = "CBBTC"

PARAMS = {
    "high_win": 60,      # trailing window (bars) for the reference high
    "dip_bps": 220,      # close must be >= this far below the trailing high
    "rsi_max": 35.0,     # Wilder RSI(14) at entry must be <= this
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
        self.avg_gain = None      # Wilder RSI(14) state
        self.avg_loss = None
        self.rsi = None
        self.entry_price = 0      # close of the bar that decided the open buy
        self.entry_seq = -1       # bar index that decided the open buy
        self.last_exit = -10 ** 9  # bar index of the last sell decision
        self.pending = None       # "buy" / "sell" while an order awaits its fill
        self.order_n = 0

    def _next_id(self, tag):
        self.order_n += 1
        return "%s%d" % (tag, self.order_n)

    def _feed_rsi(self):
        j = len(self.closes) - 1
        if j < 1:
            return
        chg = self.closes[j] - self.closes[j - 1]
        g, lo = max(chg, 0), max(-chg, 0)
        if self.avg_gain is None:
            if j >= 14:
                gains = [max(self.closes[k] - self.closes[k - 1], 0) for k in range(j - 13, j + 1)]
                losses = [max(self.closes[k - 1] - self.closes[k], 0) for k in range(j - 13, j + 1)]
                self.avg_gain = sum(gains) / 14.0
                self.avg_loss = sum(losses) / 14.0
        else:
            self.avg_gain = (self.avg_gain * 13 + g) / 14.0
            self.avg_loss = (self.avg_loss * 13 + lo) / 14.0
        if self.avg_gain is not None:
            if self.avg_loss == 0:
                self.rsi = 100.0
            else:
                rs = self.avg_gain / self.avg_loss
                self.rsi = 100.0 - 100.0 / (1.0 + rs)

    def on_batch(self, state, batch):
        for bar in batch["bars"]:
            if bar["symbol"] == SYMBOL:
                self.closes.append(int(bar["c"]))
                self.highs.append(int(bar["h"]))
                self._feed_rsi()
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
        # already printing (this close above the previous close) and
        # momentum genuinely oversold (RSI at or below RSI_MAX).
        win = self.highs[max(0, i - self.p["high_win"]):i]
        if not win:
            return None
        hi = max(win)
        if close * 10000 > hi * (10000 - self.p["dip_bps"]):
            return None  # not dipped far enough below the trailing high
        if close <= self.closes[i - 1]:
            return None  # no bounce yet — the fall may still be accelerating
        if self.rsi is None or self.rsi > self.p["rsi_max"]:
            return None  # falling, but not washed out yet
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
