# Sigma Snapback — cbBTC race bot

Entry for make.fun's **Race a BTC trading bot live for 24 hours**
(`btc-race-24h-20261117-0603`): a bot that trades Coinbase Wrapped BTC
(cbBTC) against USDC, minute by minute, on $1,000 of paper money.

## Strategy

Statistical dislocation reversion, long only, one position at a time:

- Track the last 360 closes (6 hours). Each bar, compute the z-score of
  the latest close against the mean and standard deviation of the 360
  closes before it.
- **Buy** when z ≤ −2.7σ — the price is statistically dislocated below
  its recent centre — with 75% of the cash on hand (the size keeps the
  constant-product price impact of the fill small).
- **Sell** when z recovers to +0.3σ (the dislocation has closed), if z
  falls to −6.0σ (a regime break, not a snapback), or after 480 bars
  (8 hours) in the position.

No RSI, no divergence signals, no fixed price targets: every decision
is a function of where the close sits inside its own recent
distribution. The bot is deterministic — no clock, no environment, no
randomness, no network, no files outside this repository.

## Layout

- `main.py` — the bot (Python 3.13, standard library only).
- `vendor/makefun_bars.py` — the kit's bars-replay shim, byte-identical
  to the competition kit's `sdk/makefun_bars.py`.

## Licence

MIT — see `LICENSE`.
