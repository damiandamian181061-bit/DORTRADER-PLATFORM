"""Realized volatility from candles.

Conventions, stated once:
  * Volatility is a decimal and annualized with 365 days, because crypto trades 24/7.
  * Candles live on a regular grid (`Candles`); a candle the source lacks is NaN, and any window
    that touches a NaN is NaN. Nothing is interpolated.
  * A value computed from candles up to index i is known at the *close* of candle i
    (`Candles.known_at`), never at its open. Forecast origins must use known_at.
  * Per-bar variance estimators are not annualized; `rolling_realized_vol` annualizes.
Times are int64 microseconds since the epoch.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import numpy.typing as npt
from numpy.lib.stride_tricks import sliding_window_view

from dortrader.domain import Bar

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
US = 1_000_000
SECONDS_PER_YEAR = 365 * 86_400


class Estimator(StrEnum):
    CLOSE_TO_CLOSE = "close_to_close"
    PARKINSON = "parkinson"
    GARMAN_KLASS = "garman_klass"
    ROGERS_SATCHELL = "rogers_satchell"
    YANG_ZHANG = "yang_zhang"


def _f(x: object) -> FloatArray:
    return np.asarray(x, dtype=np.float64)


def bars_per_year(step_seconds: int) -> float:
    return SECONDS_PER_YEAR / step_seconds


@dataclass(frozen=True)
class Candles:
    step_seconds: int
    start: IntArray  # candle open times
    open: FloatArray
    high: FloatArray
    low: FloatArray
    close: FloatArray

    @staticmethod
    def from_bars(bars: Sequence[Bar]) -> "Candles":
        """Place bars on a regular grid from the first to the last start; gaps become NaN."""
        if not bars:
            raise ValueError("no bars")
        step = bars[0].step_seconds
        if any(b.step_seconds != step for b in bars):
            raise ValueError("bars have different candle sizes")
        us = np.array([int(b.start.timestamp() * US) for b in bars], dtype=np.int64)
        order = np.argsort(us, kind="stable")
        us = us[order]
        size = int((us[-1] - us[0]) // (step * US)) + 1
        index = ((us - us[0]) // (step * US)).astype(np.int64)
        grid = {k: np.full(size, np.nan) for k in ("open", "high", "low", "close")}
        for column in grid:
            grid[column][index] = np.array([getattr(bars[i], column) for i in order])
        return Candles(
            step_seconds=step,
            start=us[0] + np.arange(size, dtype=np.int64) * step * US,
            open=grid["open"],
            high=grid["high"],
            low=grid["low"],
            close=grid["close"],
        )

    @property
    def known_at(self) -> IntArray:
        return self.start + self.step_seconds * US

    def log_returns(self) -> FloatArray:
        """Close-to-close log return per candle (first is NaN); NaN across a missing candle."""
        out = np.full(len(self.close), np.nan)
        out[1:] = np.log(self.close[1:] / self.close[:-1])
        return out


def close_to_close_variance(c: Candles) -> FloatArray:
    return _f(c.log_returns() ** 2)


def parkinson_variance(c: Candles) -> FloatArray:
    return _f(np.log(c.high / c.low) ** 2 / (4.0 * np.log(2.0)))


def garman_klass_variance(c: Candles) -> FloatArray:
    range_term = 0.5 * np.log(c.high / c.low) ** 2
    drift_term = (2.0 * np.log(2.0) - 1.0) * np.log(c.close / c.open) ** 2
    return _f(range_term - drift_term)


def rogers_satchell_variance(c: Candles) -> FloatArray:
    up = np.log(c.high / c.close) * np.log(c.high / c.open)
    down = np.log(c.low / c.close) * np.log(c.low / c.open)
    return _f(up + down)


def _windows(x: FloatArray, window: int) -> npt.NDArray[np.float64]:
    return sliding_window_view(x, window)


def _pad_front(values: FloatArray, window: int) -> FloatArray:
    return np.concatenate([np.full(window - 1, np.nan), values])


def rolling_variance(c: Candles, estimator: Estimator, window: int) -> FloatArray:
    """Mean per-bar variance over the trailing `window` candles, aligned to candles (NaN until
    the window is full). Not annualized."""
    if window < 2 or window > len(c.close):
        raise ValueError("window must be between 2 and the number of candles")
    if estimator is Estimator.YANG_ZHANG:
        return _yang_zhang(c, window)
    per_bar = {
        Estimator.CLOSE_TO_CLOSE: close_to_close_variance,
        Estimator.PARKINSON: parkinson_variance,
        Estimator.GARMAN_KLASS: garman_klass_variance,
        Estimator.ROGERS_SATCHELL: rogers_satchell_variance,
    }[estimator](c)
    return _pad_front(_windows(per_bar, window).mean(axis=1), window)


def _yang_zhang(c: Candles, window: int) -> FloatArray:
    overnight = np.full(len(c.close), np.nan)
    overnight[1:] = np.log(c.open[1:] / c.close[:-1])  # gap from the previous close
    open_to_close = np.log(c.close / c.open)
    k = 0.34 / (1.34 + (window + 1) / (window - 1))
    sigma_o = _windows(overnight, window).var(axis=1, ddof=1)
    sigma_c = _windows(open_to_close, window).var(axis=1, ddof=1)
    sigma_rs = _windows(rogers_satchell_variance(c), window).mean(axis=1)
    return _pad_front(sigma_o + k * sigma_c + (1.0 - k) * sigma_rs, window)


def rolling_realized_vol(c: Candles, estimator: Estimator, window: int) -> FloatArray:
    """Annualized realized vol over the trailing `window` candles, known at `c.known_at`."""
    with np.errstate(invalid="ignore"):
        variance = rolling_variance(c, estimator, window)
        return _f(np.sqrt(np.maximum(variance, 0.0) * bars_per_year(c.step_seconds)))


@dataclass(frozen=True)
class DailyVol:
    day_start: IntArray  # 00:00 UTC of each complete day
    variance: FloatArray  # sum of squared candle returns in the day (not annualized)
    vol: FloatArray  # annualized from that day alone


def daily_realized_vol(c: Candles) -> DailyVol:
    """Realized variance per UTC day from intraday returns, for complete days only: a day with a
    missing candle, or a first candle with no previous close, is left out rather than patched."""
    per_day = 86_400 // c.step_seconds
    if per_day * c.step_seconds != 86_400:
        raise ValueError("candle size must divide a day")
    r = c.log_returns()
    day = c.start // (86_400 * US)
    days = np.unique(day)
    starts, variances = [], []
    for d in days:
        in_day = r[day == d]
        if len(in_day) == per_day and np.isfinite(in_day).all():
            starts.append(d * 86_400 * US)
            variances.append(float(np.sum(in_day**2)))
    variance = np.array(variances, dtype=np.float64)
    return DailyVol(
        day_start=np.array(starts, dtype=np.int64),
        variance=variance,
        vol=np.sqrt(variance * 365.0),
    )


def forward_realized_vol(returns: FloatArray, horizon: int, step_seconds: int) -> FloatArray:
    """For an origin at the close of candle i, the annualized realized vol of the next `horizon`
    returns (candles i+1 .. i+horizon). NaN where those are not all available. This is the
    quantity forecasts are scored against."""
    n = len(returns)
    out = np.full(n, np.nan)
    if horizon < 1 or horizon >= n:
        return out
    squares = returns**2
    sums = _windows(squares[1:], horizon).sum(axis=1)  # window j covers candles j+1 .. j+horizon
    out[: len(sums)] = np.sqrt(sums * bars_per_year(step_seconds) / horizon)
    return out
