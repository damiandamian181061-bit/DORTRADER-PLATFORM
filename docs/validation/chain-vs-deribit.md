# Our implied vol versus Deribit mark_iv, out-of-the-money contracts

> **Preliminary.** Produced on 2026-10-18 from a recording of about 100 seconds (11 snapshots), only to prove the tool works on real data. Rerun `scripts/compare_chain_to_deribit.py` over days of recorded data before drawing conclusions. Largest gaps sit near the money in the shortest expiries, where the price is most sensitive to the small time offset between Deribit's mark price and its forward.

Range 2026-10-18T20:00:51.474648+00:00 to 2026-10-18T20:02:52.381986+00:00, 13 snapshots, every 10 s. Differences in vol points (ours minus Deribit's).

| Log-moneyness | Days to expiry | n | Mean | Median abs | P90 abs | P99 abs | Max abs |
|---|---|---|---|---|---|---|---|
| -inf to -0.1  | 0 to 1 d | 39 | -0.004 | 0.035 | 0.082 | 0.114 | 0.114 |
| -inf to -0.1  | 1 to 3 d | 52 | -0.010 | 0.043 | 0.103 | 0.127 | 0.127 |
| -inf to -0.1  | 3 to 10 d | 52 | -0.007 | 0.031 | 0.070 | 0.095 | 0.095 |
| -inf to -0.1  | 10 to 45 d | 481 | +0.002 | 0.005 | 0.019 | 0.032 | 0.036 |
| -inf to -0.1  | 45 to inf d | 247 | +0.000 | 0.004 | 0.010 | 0.013 | 0.016 |
| -0.1 to -0.03  | 0 to 1 d | 66 | -0.019 | 0.055 | 0.158 | 0.276 | 0.276 |
| -0.1 to -0.03  | 1 to 3 d | 143 | -0.014 | 0.053 | 0.141 | 0.180 | 0.200 |
| -0.1 to -0.03  | 3 to 10 d | 128 | -0.010 | 0.044 | 0.099 | 0.146 | 0.151 |
| -0.1 to -0.03  | 10 to 45 d | 130 | +0.001 | 0.007 | 0.030 | 0.041 | 0.051 |
| -0.1 to -0.03  | 45 to inf d | 52 | +0.001 | 0.007 | 0.014 | 0.033 | 0.033 |
| -0.03 to 0.03  | 0 to 1 d | 129 | +0.012 | 0.112 | 0.338 | 0.537 | 0.684 |
| -0.03 to 0.03  | 1 to 3 d | 247 | +0.002 | 0.099 | 0.247 | 0.361 | 0.410 |
| -0.03 to 0.03  | 3 to 10 d | 184 | +0.003 | 0.072 | 0.166 | 0.281 | 0.284 |
| -0.03 to 0.03  | 10 to 45 d | 130 | +0.001 | 0.012 | 0.049 | 0.070 | 0.113 |
| -0.03 to 0.03  | 45 to inf d | 39 | -0.000 | 0.009 | 0.021 | 0.029 | 0.029 |
| 0.03 to 0.1  | 0 to 1 d | 104 | +0.023 | 0.050 | 0.132 | 0.210 | 0.237 |
| 0.03 to 0.1  | 1 to 3 d | 221 | +0.016 | 0.043 | 0.125 | 0.169 | 0.186 |
| 0.03 to 0.1  | 3 to 10 d | 182 | +0.012 | 0.039 | 0.085 | 0.141 | 0.153 |
| 0.03 to 0.1  | 10 to 45 d | 156 | -0.002 | 0.009 | 0.033 | 0.053 | 0.064 |
| 0.03 to 0.1  | 45 to inf d | 52 | -0.001 | 0.007 | 0.017 | 0.021 | 0.021 |
| 0.1 to inf  | 1 to 3 d | 65 | +0.012 | 0.030 | 0.075 | 0.087 | 0.087 |
| 0.1 to inf  | 3 to 10 d | 117 | +0.006 | 0.022 | 0.051 | 0.069 | 0.075 |
| 0.1 to inf  | 10 to 45 d | 455 | -0.001 | 0.005 | 0.019 | 0.028 | 0.036 |
| 0.1 to inf  | 45 to inf d | 377 | +0.000 | 0.004 | 0.009 | 0.015 | 0.015 |
