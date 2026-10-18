# Black-76 against Deribit tickers

Captured 2026-10-18 from public `ticker` calls (fixtures in `tests/fixtures/deribit/`). Our inputs: forward = `underlying_price`, sigma = `mark_iv` / 100, rate = `interest_rate` (0), T = ACT/365 from the ticker timestamp to the 08:00 UTC expiry. Tests: `tests/golden/test_deribit_replication.py`.

| Option | Days left | Price ours / mark | IV from mark / `mark_iv` | Delta ours / theirs | Vega ours / theirs | Theta ours / theirs |
|---|---|---|---|---|---|---|
| 20OCT26-81000-P | 1.529 | 722.17 / 722.23 | 36.873 / 36.87 | -0.47439 / -0.47439 | 20.9007 / 20.89884 | -251.925 / -251.958 |
| 20OCT26-72000-C (deep ITM) | 1.529 | 9112.60 / 9112.38 | 77.40 / 77.59 | 0.99169 / 0.99169 | 1.1897 / 1.18916 | -30.178 / -30.170 |
| 19OCT26-81000-C | 0.520 | 400.23 / 391.19 | 36.60 / 37.34 | 0.46281 / 0.46280 | 12.1297 / 12.12832 | -226.461 / -226.432 (see note) |
| 19OCT26-83000-C | 0.520 | 10.49 / 10.22 | 35.02 / 35.17 | 0.02585 / 0.02583 | 1.8350 / 1.83387 | -10.22 / -10.219 (see note) |

Reading the table:

- **Greeks agree within rounding.** Deribit rounds `mark_iv` to 0.01 vol points and Greeks to 5 decimals. The remaining gaps in delta, vega and theta are the size that rounding predicts.
- **Prices agree exactly only when forward and mark are simultaneous.** The 19OCT26-81000-C gap of 9.04 USDC corresponds to a 19.5 USDC (2.4 bps) difference between the forward Deribit used for the mark and the `underlying_price` returned beside it. The Greeks, which depend on the forward and `mark_iv` only, still match.
- **Theta inside the last day.** Our per-day theta for 19OCT26-81000-C is -435.29. Deribit reports -226.43, which is our value times 0.520 (the fraction of a day left). For 19OCT26-83000-C Deribit reports minus the mark price (-10.22), a cap at the option's value. `theta_deribit_style` reproduces both; plain per-day theta does not.
- **Rho** was not matched by a plain Black-76 rho and is not offered.

The test bounds are built from these sources (IV rounding, output rounding, 5 bps of forward simultaneity), not tuned to pass. The 5 bps budget is about twice the largest gap seen.
