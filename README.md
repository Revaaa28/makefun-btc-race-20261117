# BTC Race Bot — cbBTC 1-minute, 24-hour live race

[![make.fun · btc-race-24h-20261117-0603](https://make.fun/badge/btc-race-24h-20261117-0603.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![rank](https://make.fun/badge/btc-race-24h-20261117-0603/Revaaa28/makefun-btc-race-20261117/rank.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![reward](https://make.fun/badge/btc-race-24h-20261117-0603/Revaaa28/makefun-btc-race-20261117/reward.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)

A trading bot (`main.py`, Python 3.13, standard library only) that races
Coinbase Wrapped BTC (cbBTC) on 1-minute on-chain prices with $1,000 of paper
money, built for the make.fun bounty
[btc-race-24h-20261117-0603](https://make.fun/bounty/btc-race-24h-20261117-0603)
("Race a BTC trading bot live for 24 hours").

## Strategy

Long-only, selective deep-dip buying — no RSI, no divergence, no moving
averages:

- **Signal.** A buy is considered only when the latest close sits at least
  220 bps below the trailing 60-bar high of bar highs — a sharp one-hour
  washout, not a slow grind — AND the latest bar bounced (its close is above
  the previous close), so the fall is already exhausting instead of still
  accelerating.
- **Exit.** Sell everything when the close falls 60 bps below the entry
  bar's close (the bounce failed) or when 420 bars (7 hours) have passed
  since the entry bar (the bounce is given a fixed window, then banked).
- **Risk.** All-in per entry, one position at a time, 15-bar cooldown after
  every exit.
- **Deterministic.** No clock, no network, no randomness, no environment
  reads — two runs on the same bars produce byte-identical orders.

## Validation

The bot was backtested against the competition's own published dev window
(1,440 one-minute bars) using the harness's exact fill model — fills at the
next bar's open, 4 bps fee, 2 bps slippage, constant-product price impact
against the bar's pool depth, positions flattened at the end:

- Net PnL **+$14.65** on $1,000 (clears the dev gate: PnL > $0)
- Max drawdown **0.62%** (race bar: ≤ 25%)
- 2 trades, 1 round trip (bars 564 -> 984)
- Deterministic: two stdio runs produce byte-identical action logs

The strategy was then re-validated on eight additional 24-hour windows of
real BTC 1-minute data covering a −13.7% crash week. The same configuration
is net-positive across all nine windows combined (dev +$14.65, other windows
+$31 total, worst window −$9.49). Every trend, breakout, regime and
short-horizon mean-reversion family tested lost money after costs on the dev
window; looser dip thresholds and two-tier variants caught falling knives on
crash days and were discarded. The parameters sit mid-plateau: dip 200–220
bps, time stop 300–420 bars and stop 60–90 bps all score +$8 to +$15 on dev.

## Layout

- `main.py` — the bot (strategy + protocol loop via the vendored shim)
- `vendor/makefun_bars.py` — the competition's Python shim for the
  `agent-fun/bars-replay/v1` protocol, vendored per its own instructions

## Run it

```sh
python3 -P -s main.py
```

The bot speaks `agent-fun/bars-replay/v1` (newline-delimited JSON on stdin
and stdout): one `hello`, one `batch` per minute, one reply per batch.
