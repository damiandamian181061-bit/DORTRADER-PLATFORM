# Volatility forecast baselines

Series `BTC_USDC-PERPETUAL`, hourly candles from 2022-03-09 to 2026-10-18 21:00 UTC (40,424 candles). Forecasts were scored out of sample from 2025-10-18 to the end of the data. Generated 2026-10-18 20:37 UTC from data `8415752f44d6c524`.

**How to read it.** Each model forecasts the annualized realized volatility over the next 24 hours, 7 days or 30 days, seeing only data up to the forecast time. QLIKE is the loss (lower is better, zero is a perfect forecast). The gain column is the naive baseline's QLIKE minus the model's, with a bootstrap interval; a model is called better only if the whole interval is above zero. MZ slope and R2 come from regressing realized on forecast variance (slope near 1 means well calibrated). Forecasts are spaced one horizon apart so scored windows do not overlap, which leaves few points at the longer horizons and wide intervals.

The naive model repeats the last window's realized vol. `dvol_30d_implied` is Deribit's 30-day implied volatility index used as a forecast; it is a 30-day measure, so it is only like-for-like at the 30-day horizon.

### Forecasting the next 24 hours

365 scored forecasts, one every 24 hours.

| Model | QLIKE | MAE (vol pts) | Bias (vol pts) | MZ slope | MZ R2 | QLIKE gain vs naive (95% CI) | Verdict |
|---|---|---|---|---|---|---|---|
| har | 0.418 | 15.1 | +6.4 | 0.899 | 0.204 | +0.512 (+0.310, +0.754) | better than naive |
| dvol_30d_implied | 0.425 | 14.4 | +4.7 | 1.568 | 0.229 | +0.505 (+0.296, +0.759) | better than naive |
| ewma_fitted | 0.461 | 14.8 | +4.0 | 0.749 | 0.113 | +0.469 (+0.258, +0.721) | better than naive |
| ewma_riskmetrics | 0.474 | 15.0 | +4.4 | 0.790 | 0.076 | +0.456 (+0.237, +0.718) | better than naive |
| gjr_garch | 0.498 | 19.3 | +13.4 | 0.356 | 0.167 | +0.433 (+0.221, +0.688) | better than naive |
| garch | 0.499 | 19.4 | +13.5 | 0.353 | 0.164 | +0.432 (+0.221, +0.687) | better than naive |
| trailing_30d | 0.504 | 15.2 | +4.3 | 0.601 | 0.053 | +0.426 (+0.199, +0.691) | better than naive |
| naive | 0.930 | 16.0 | +0.0 | 0.432 | 0.186 | baseline | baseline |

### Forecasting the next 7 days

52 scored forecasts, one every 168 hours.

| Model | QLIKE | MAE (vol pts) | Bias (vol pts) | MZ slope | MZ R2 | QLIKE gain vs naive (95% CI) | Verdict |
|---|---|---|---|---|---|---|---|
| dvol_30d_implied | 0.144 | 8.5 | +1.5 | 0.769 | 0.129 | +0.119 (-0.007, +0.354) | no clear difference |
| har | 0.148 | 9.7 | +3.7 | 1.050 | 0.146 | +0.115 (-0.033, +0.372) | no clear difference |
| ewma_fitted | 0.191 | 9.7 | +1.3 | 0.379 | 0.021 | +0.072 (-0.069, +0.305) | no clear difference |
| ewma_riskmetrics | 0.195 | 9.1 | +1.5 | 0.435 | 0.062 | +0.067 (-0.038, +0.239) | no clear difference |
| trailing_30d | 0.221 | 9.0 | +1.2 | 0.395 | 0.059 | +0.042 (-0.077, +0.208) | no clear difference |
| naive | 0.263 | 9.5 | +0.2 | 0.260 | 0.067 | baseline | baseline |
| gjr_garch | 0.381 | 23.0 | +22.2 | 1.258 | 0.362 | -0.118 (-0.333, +0.186) | no clear difference |
| garch | 0.387 | 23.3 | +22.5 | 1.245 | 0.357 | -0.124 (-0.340, +0.181) | no clear difference |

### Forecasting the next 30 days

12 scored forecasts, one every 720 hours.

| Model | QLIKE | MAE (vol pts) | Bias (vol pts) | MZ slope | MZ R2 | QLIKE gain vs naive (95% CI) | Verdict |
|---|---|---|---|---|---|---|---|
| dvol_30d_implied | 0.102 | 7.6 | +0.4 | 0.475 | 0.093 | +0.136 (+0.055, +0.225) | better than naive |
| har | 0.124 | 8.7 | +5.3 | 0.018 | 0.000 | +0.114 (-0.013, +0.271) | no clear difference |
| ewma_fitted | 0.131 | 9.1 | +0.8 | -0.437 | 0.034 | +0.107 (-0.005, +0.238) | no clear difference |
| ewma_riskmetrics | 0.178 | 10.3 | +0.3 | 0.163 | 0.022 | +0.061 (+0.023, +0.107) | better than naive |
| naive | 0.238 | 11.8 | +0.3 | 0.013 | 0.000 | baseline | baseline |
| trailing_30d | 0.238 | 11.8 | +0.3 | 0.013 | 0.000 | +0.000 (+0.000, +0.000) | no clear difference |
| gjr_garch | 0.466 | 30.2 | +30.2 | 0.621 | 0.310 | -0.227 (-0.434, +0.034) | no clear difference |
| garch | 0.475 | 30.7 | +30.7 | 0.632 | 0.317 | -0.237 (-0.444, +0.023) | no clear difference |

## Implied versus realized, 30 days

Over 336 daily origins in the test window, DVOL averaged 44.1 and the realized volatility of the following 30 days averaged 43.1 (vol points). The mean gap in variance terms (implied minus realized) was +0.0043. Origins one day apart overlap, so this is a descriptive average, not an independent sample.

## GARCH diagnostics

Fitted on the latest 17,520 hourly returns: alpha 0.162, beta 0.827, persistence 0.9885. Its multi-step forecasts head toward the fitted long-run volatility of 76 vol points, while the realized volatility over the same returns was 45. Hourly returns are heavy-tailed, which a Gaussian GARCH reads as a high long-run variance; a Student-t variant was tried on three origins and fitted persistence of 1.0, so it was not added.

## Cross-check on `BTC-PERPETUAL`

The same evaluation on the inverse perpetual (candles from 2021-01-01); conclusions should agree if they are not artefacts of one series.

### Forecasting the next 24 hours

365 scored forecasts, one every 24 hours.

| Model | QLIKE | MAE (vol pts) | Bias (vol pts) | MZ slope | MZ R2 | QLIKE gain vs naive (95% CI) | Verdict |
|---|---|---|---|---|---|---|---|
| dvol_30d_implied | 0.434 | 14.6 | +4.8 | 1.588 | 0.229 | +0.504 (+0.298, +0.748) | better than naive |
| har | 0.457 | 16.5 | +8.8 | 1.109 | 0.182 | +0.482 (+0.279, +0.725) | better than naive |
| ewma_fitted | 0.472 | 15.1 | +3.6 | 0.686 | 0.148 | +0.466 (+0.266, +0.705) | better than naive |
| ewma_riskmetrics | 0.484 | 15.0 | +4.5 | 0.788 | 0.075 | +0.455 (+0.240, +0.709) | better than naive |
| gjr_garch | 0.510 | 19.6 | +13.8 | 0.355 | 0.172 | +0.429 (+0.226, +0.675) | better than naive |
| garch | 0.511 | 19.7 | +13.9 | 0.352 | 0.169 | +0.427 (+0.225, +0.674) | better than naive |
| trailing_30d | 0.515 | 15.3 | +4.4 | 0.602 | 0.053 | +0.423 (+0.196, +0.684) | better than naive |
| naive | 0.938 | 16.2 | +0.0 | 0.429 | 0.184 | baseline | baseline |

### Forecasting the next 7 days

52 scored forecasts, one every 168 hours.

| Model | QLIKE | MAE (vol pts) | Bias (vol pts) | MZ slope | MZ R2 | QLIKE gain vs naive (95% CI) | Verdict |
|---|---|---|---|---|---|---|---|
| dvol_30d_implied | 0.148 | 8.7 | +1.5 | 0.777 | 0.129 | +0.127 (-0.007, +0.377) | no clear difference |
| har | 0.167 | 10.9 | +5.8 | 0.815 | 0.125 | +0.108 (-0.051, +0.381) | no clear difference |
| ewma_fitted | 0.189 | 10.1 | +1.7 | 0.386 | 0.019 | +0.086 (-0.072, +0.346) | no clear difference |
| ewma_riskmetrics | 0.201 | 9.2 | +1.5 | 0.438 | 0.063 | +0.074 (-0.039, +0.258) | no clear difference |
| trailing_30d | 0.229 | 9.1 | +1.2 | 0.395 | 0.059 | +0.046 (-0.078, +0.221) | no clear difference |
| naive | 0.275 | 9.7 | +0.2 | 0.258 | 0.066 | baseline | baseline |
| gjr_garch | 0.409 | 24.2 | +23.6 | 1.189 | 0.380 | -0.134 (-0.362, +0.191) | no clear difference |
| garch | 0.415 | 24.6 | +24.0 | 1.183 | 0.377 | -0.140 (-0.368, +0.185) | no clear difference |

### Forecasting the next 30 days

12 scored forecasts, one every 720 hours.

| Model | QLIKE | MAE (vol pts) | Bias (vol pts) | MZ slope | MZ R2 | QLIKE gain vs naive (95% CI) | Verdict |
|---|---|---|---|---|---|---|---|
| dvol_30d_implied | 0.105 | 7.7 | +0.4 | 0.483 | 0.094 | +0.144 (+0.057, +0.240) | better than naive |
| ewma_fitted | 0.138 | 9.5 | +0.6 | 0.003 | 0.000 | +0.110 (-0.003, +0.245) | no clear difference |
| har | 0.143 | 9.9 | +7.6 | 0.136 | 0.005 | +0.106 (-0.041, +0.288) | no clear difference |
| ewma_riskmetrics | 0.184 | 10.4 | +0.3 | 0.162 | 0.022 | +0.065 (+0.023, +0.115) | better than naive |
| naive | 0.249 | 11.9 | +0.2 | 0.012 | 0.000 | baseline | baseline |
| trailing_30d | 0.249 | 11.9 | +0.2 | 0.012 | 0.000 | +0.000 (+0.000, +0.000) | no clear difference |
| gjr_garch | 0.513 | 32.8 | +32.8 | 0.554 | 0.335 | -0.264 (-0.477, +0.006) | no clear difference |
| garch | 0.523 | 33.3 | +33.3 | 0.571 | 0.349 | -0.274 (-0.488, -0.007) | worse than naive |
