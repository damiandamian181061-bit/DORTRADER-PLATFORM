import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from dortrader.domain import Bar
from dortrader.volatility.realized import (
    SECONDS_PER_YEAR,
    US,
    Candles,
    Estimator,
    bars_per_year,
    daily_realized_vol,
    forward_realized_vol,
    rolling_realized_vol,
)
from tests.conftest import load_json

HOUR = 3600
SIGMA = 0.6


def simulate(
    sigma: float, n: int, *, step: int = HOUR, substeps: int = 600, seed: int = 1
) -> Candles:
    """Candles from a finely sampled geometric Brownian motion with known annualized vol."""
    rng = np.random.default_rng(seed)
    dt = step / SECONDS_PER_YEAR / substeps
    increments = sigma * math.sqrt(dt) * rng.standard_normal((n, substeps))
    level = np.cumsum(increments.reshape(-1)).reshape(n, substeps)  # log price at every sub-step
    previous = np.concatenate([[0.0], level[:-1, -1]])  # log price at the start of each candle
    return Candles(
        step_seconds=step,
        start=np.arange(n, dtype=np.int64) * step * US,
        open=100 * np.exp(previous),
        high=100 * np.exp(np.maximum(level.max(axis=1), previous)),
        low=100 * np.exp(np.minimum(level.min(axis=1), previous)),
        close=100 * np.exp(level[:, -1]),
    )


def last(c: Candles, estimator: Estimator, window: int) -> float:
    return float(rolling_realized_vol(c, estimator, window)[-1])


def test_close_to_close_recovers_the_true_vol_within_sampling_error() -> None:
    c = simulate(SIGMA, 8760)
    standard_error = 1 / math.sqrt(2 * 8000)  # relative, for n independent returns

    estimate = last(c, Estimator.CLOSE_TO_CLOSE, 8000)

    assert abs(estimate / SIGMA - 1) < 4 * standard_error


@pytest.mark.parametrize(
    "estimator",
    [Estimator.PARKINSON, Estimator.GARMAN_KLASS, Estimator.ROGERS_SATCHELL, Estimator.YANG_ZHANG],
)
def test_range_based_estimators_are_close_but_biased_low_by_discrete_sampling(
    estimator: Estimator,
) -> None:
    fine = last(simulate(SIGMA, 8760, substeps=600), estimator, 8000) / SIGMA - 1
    coarse = last(simulate(SIGMA, 8760, substeps=60), estimator, 8000) / SIGMA - 1

    # The high and low of a path sampled only a few hundred times miss the true extremes, so
    # these estimators read low, and more so the coarser the sampling.
    assert -0.06 < fine < 0.01
    assert coarse < fine - 0.02


def test_range_estimators_are_steadier_than_close_to_close() -> None:
    c = simulate(SIGMA, 8760)

    parkinson = rolling_realized_vol(c, Estimator.PARKINSON, 720)
    close_to_close = rolling_realized_vol(c, Estimator.CLOSE_TO_CLOSE, 720)

    assert np.nanstd(parkinson) < 0.6 * np.nanstd(close_to_close)


def test_rolling_output_is_aligned_to_candles_and_known_only_at_the_close() -> None:
    c = simulate(SIGMA, 200, substeps=20)

    vol = rolling_realized_vol(c, Estimator.PARKINSON, 24)

    assert len(vol) == 200
    assert np.isnan(vol[:23]).all() and np.isfinite(vol[23:]).all()
    assert (c.known_at - c.start == HOUR * US).all()


def test_a_missing_candle_poisons_only_the_windows_that_touch_it() -> None:
    c = simulate(SIGMA, 100, substeps=20)
    broken = Candles(
        c.step_seconds, c.start, c.open.copy(), c.high.copy(), c.low.copy(), c.close.copy()
    )
    for column in (broken.open, broken.high, broken.low, broken.close):
        column[50] = np.nan

    parkinson = rolling_realized_vol(broken, Estimator.PARKINSON, 10)
    close_to_close = rolling_realized_vol(broken, Estimator.CLOSE_TO_CLOSE, 10)

    assert np.isnan(parkinson[50:60]).all() and np.isfinite(parkinson[60:]).all()
    assert np.isfinite(parkinson[9:50]).all()
    # A close-to-close return needs two closes, so the hole also removes the next return.
    assert np.isnan(close_to_close[50:61]).all() and np.isfinite(close_to_close[61:]).all()


def test_flat_prices_have_zero_volatility() -> None:
    flat = np.full(50, 100.0)
    c = Candles(HOUR, np.arange(50, dtype=np.int64) * HOUR * US, flat, flat, flat, flat)

    for estimator in Estimator:
        assert last(c, estimator, 10) == 0.0


def test_window_must_fit_the_data() -> None:
    c = simulate(SIGMA, 20, substeps=5)

    with pytest.raises(ValueError, match="window"):
        rolling_realized_vol(c, Estimator.PARKINSON, 1)
    with pytest.raises(ValueError, match="window"):
        rolling_realized_vol(c, Estimator.PARKINSON, 21)


def bar(start: datetime, close: float) -> Bar:
    return Bar(
        instrument="X",
        step_seconds=HOUR,
        start=start,
        open=close,
        high=close,
        low=close,
        close=close,
    )


def test_candles_from_bars_fill_holes_with_nan_and_sort() -> None:
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    bars = [bar(t0 + timedelta(hours=h), 100.0 + h) for h in (3, 0, 1, 4)]  # hour 2 is missing

    c = Candles.from_bars(bars)

    assert len(c.close) == 5 and np.isnan(c.close[2])
    assert c.close[[0, 1, 3, 4]].tolist() == [100.0, 101.0, 103.0, 104.0]
    assert np.isnan(c.log_returns()[[0, 2, 3]]).all()
    with pytest.raises(ValueError, match="no bars"):
        Candles.from_bars([])
    other = bars[0].model_copy(update={"step_seconds": 60})
    with pytest.raises(ValueError, match="different candle sizes"):
        Candles.from_bars([bars[1], other])


def test_daily_realized_vol_uses_complete_days_only() -> None:
    # Three days of hourly candles starting at 06:00 on day one: the first day has no full set
    # of returns, and a hole on day two removes that day too.
    t0 = datetime(2026, 10, 1, 6, tzinfo=UTC)
    a = 0.01
    price, bars = 100.0, []
    for h in range(24 * 4):
        price *= math.exp(a if h % 2 == 0 else -a)
        if h != 30:  # hole inside the second day
            bars.append(bar(t0 + timedelta(hours=h), price))
    c = Candles.from_bars(bars)

    daily = daily_realized_vol(c)

    days = [datetime.fromtimestamp(d / US, UTC).day for d in daily.day_start]
    assert days == [3, 4]  # days 1 (partial) and 2 (hole) are left out; 5 is cut short
    assert daily.variance == pytest.approx([24 * a * a] * 2)
    assert daily.vol == pytest.approx(math.sqrt(24 * a * a * 365), rel=1e-12)


def test_forward_realized_vol_covers_the_next_returns_only() -> None:
    returns = np.array([np.nan, 0.01, 0.02, 0.03, 0.04])
    per_year = bars_per_year(HOUR)

    out = forward_realized_vol(returns, horizon=2, step_seconds=HOUR)

    assert out[0] == pytest.approx(math.sqrt((0.01**2 + 0.02**2) * per_year / 2))
    assert out[1] == pytest.approx(math.sqrt((0.02**2 + 0.03**2) * per_year / 2))
    assert out[2] == pytest.approx(math.sqrt((0.03**2 + 0.04**2) * per_year / 2))
    assert np.isnan(out[3:]).all()
    assert np.isnan(forward_realized_vol(returns, horizon=9, step_seconds=HOUR)).all()


def test_real_daily_candles_give_plausible_volatility() -> None:
    result = load_json("deribit", "chart_btc_usdc_perp_1d.json")["result"]
    bars = [
        Bar(
            instrument="BTC_USDC-PERPETUAL",
            step_seconds=86_400,
            start=datetime.fromtimestamp(t / 1000, UTC),
            open=o,
            high=h,
            low=lo,
            close=cl,
        )
        for t, o, h, lo, cl in zip(
            result["ticks"],
            result["open"],
            result["high"],
            result["low"],
            result["close"],
            strict=True,
        )
    ]
    c = Candles.from_bars(bars)

    cc = rolling_realized_vol(c, Estimator.CLOSE_TO_CLOSE, 30)
    parkinson = rolling_realized_vol(c, Estimator.PARKINSON, 30)

    valid = np.isfinite(cc) & np.isfinite(parkinson)
    assert valid.sum() > 300
    assert 0.10 < cc[valid].min() and cc[valid].max() < 1.50
    assert np.corrcoef(cc[valid], parkinson[valid])[0, 1] > 0.7
