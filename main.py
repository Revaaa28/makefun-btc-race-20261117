"""Sigma Snapback - a statistical dislocation reversion bot for cbBTC.

Strategy (long only, one position, no indicators beyond dispersion):

  Keep the last WINDOW closes. Each bar, measure how far the latest
  close sits from the trailing mean of the WINDOW closes before it,
  in units of that window's own standard deviation (a z-score,
  tracked in tenths so the arithmetic stays exact):

    z10 = (close - mean) * 10 / stdev

  - Flat and z10 <= ENTRY_Z10 (-27, i.e. -2.7 sigma): the price is
    statistically dislocated below its recent centre. Buy with
    SIZE_BPS of the cash on hand. Deep dislocations snap back far
    more reliably than shallow ones, so the threshold is strict.
  - Holding and z10 >= EXIT_Z10 (+3, +0.3 sigma): the dislocation
    has closed. Sell everything.
  - Holding and z10 <= STOP_Z10 (-60, -6.0 sigma): the dislocation
    became a regime break, not a snapback. Sell everything.
  - Holding for TIME_STOP bars: sell everything; a reversion that
    has not happened in eight hours is not coming on this horizon.

  No RSI, no divergence, no fixed price targets: entries and exits
  are decided purely by where the close sits inside its own recent
  distribution. Position size is 75% of cash so the constant-product
  price impact of each fill stays small against the pool depth.

  Deterministic: a pure function of the bars seen so far. No clock,
  no environment, no randomness, no files, no network.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

WINDOW = 360          # closes in the reference distribution (6 hours)
ENTRY_Z10 = -27       # buy at z <= -2.7 sigma
EXIT_Z10 = 3          # sell at z >= +0.3 sigma
STOP_Z10 = -60        # disaster exit at z <= -6.0 sigma
TIME_STOP = 480       # sell after 480 bars in the position (8 hours)
SIZE_BPS = 7500       # 75% of cash per entry
MAX_SLIPPAGE_BPS = 10000

_closes = {}          # symbol -> list of closes (ints), oldest first
_held_bars = {}       # symbol -> batches since the buy decision


def _z10(closes):
    """z of the latest close against the WINDOW closes before it, in tenths."""
    if len(closes) <= WINDOW:
        return None
    window = closes[-WINDOW - 1:-1]
    mean = sum(window) / WINDOW
    var = sum((x - mean) ** 2 for x in window) / WINDOW
    sd = var ** 0.5
    if sd <= 0:
        return None
    return int((closes[-1] - mean) * 10 / sd)


def on_batch(state, batch):
    orders = []
    for bar in batch["bars"]:
        symbol = bar["symbol"]
        closes = _closes.setdefault(symbol, [])
        closes.append(int(bar["c"]))
        if state.warming_up:
            continue
        z = _z10(closes)
        if z is None:
            continue
        held = state.positions.get(symbol)
        if held is None:
            _held_bars[symbol] = 0
            if z <= ENTRY_Z10:
                notional = state.cash * SIZE_BPS // 10000
                if notional > 0:
                    orders.append({
                        "side": "buy",
                        "symbol": symbol,
                        "notional_quote_units": notional,
                        "max_slippage_bps": MAX_SLIPPAGE_BPS,
                    })
        else:
            _held_bars[symbol] = _held_bars.get(symbol, 0) + 1
            if z >= EXIT_Z10 or z <= STOP_Z10 or _held_bars[symbol] >= TIME_STOP:
                orders.append({
                    "side": "sell",
                    "symbol": symbol,
                    "qty": held.qty,
                    "max_slippage_bps": MAX_SLIPPAGE_BPS,
                })
                _held_bars[symbol] = 0
    return orders


makefun_bars.run(on_batch)
