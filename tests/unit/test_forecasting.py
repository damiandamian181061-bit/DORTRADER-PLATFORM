import math

import numpy as np
import pytest

from dortrader.forecasting import (
    Ewma,
    ExternalForecast,
    Garch,
    Har,
    NaiveLast,
    TrailingMean,
    default_models,
    evaluate,
    rolling_origin,
)
from dortrader.forecasting.evaluate import (
    mincer_zarnowitz,
    paired_bootstrap,
    qlike,
    score_points,
)
from dortrader.forecasting.models import HOURS_PER_YEAR, FloatArray
from dortrader.volatility.realized import US, forward_realized_vol

HOUR_US = 3600 * US


def constant_vol_returns(annual_vol: float, n: int, seed: int = 0) -> FloatArray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(n) * annual_vol / math.sqrt(HOURS_PER_YEAR)


def simulate_garch(
    n: int, omega: float = 0.002, alpha: float = 0.08, beta: float = 0.90, seed: int = 0
) -> FloatArray:
    """Hourly returns (as decimals) from a GARCH(1,1) whose parameters are in percent units."""
    rng = np.random.default_rng(seed)
    z = rng.standard_normal(n)
    variance = omega / (1 - alpha - beta)
    out = np.empty(n)
    for i in range(n):
        out[i] = math.sqrt(variance) * z[i]
        variance = omega + alpha * out[i] ** 2 + beta * variance
    return out / 100.0


def known_at(n: int) -> np.ndarray:
    return (np.arange(n, dtype=np.int64) + 1) * HOUR_US


def test_every_model_finds_a_constant_volatility() -> None:
    returns = constant_vol_returns(0.5, 6000)
    history = returns[:5000]

    for model in (NaiveLast(), TrailingMean(720), Ewma(), Ewma(fitted=True), Garch(), Har()):
        model.fit(history, 24)
        forecast = model.predict(history, 24)
        assert forecast == pytest.approx(0.5, rel=0.12), model.name


def test_ewma_reacts_to_a_regime_change_faster_than_the_monthly_average() -> None:
    quiet = constant_vol_returns(0.3, 3000, seed=1)
    wild = constant_vol_returns(1.2, 120, seed=2)  # five days of much higher vol
    history = np.concatenate([quiet, wild])

    ewma = Ewma().predict(history, 24)
    monthly = TrailingMean(720).predict(history, 24)

    assert abs(ewma - 1.2) < abs(monthly - 1.2)
    assert ewma > monthly  # and neither has fully caught up with the new level


def test_garch_recovers_known_parameters() -> None:
    returns = simulate_garch(25_000)

    model = Garch()
    model.fit(returns, 24)

    parameters = model.parameters
    assert parameters["alpha[1]"] == pytest.approx(0.08, abs=0.03)
    assert parameters["beta[1]"] == pytest.approx(0.90, abs=0.04)
    assert parameters["omega"] == pytest.approx(0.002, abs=0.002)


def test_gjr_garch_fits_and_forecasts() -> None:
    returns = simulate_garch(8000, seed=3)
    model = Garch(asymmetric=True)

    model.fit(returns, 24)

    assert "gamma[1]" in model.parameters
    assert 0.05 < model.predict(returns, 168) < 5.0


def test_har_forecasts_between_calm_and_stormy_levels() -> None:
    quiet = constant_vol_returns(0.3, 4000, seed=4)
    storm = constant_vol_returns(0.9, 100, seed=5)
    history = np.concatenate([quiet, storm])
    model = Har()

    model.fit(history, 24)
    forecast = model.predict(history, 24)

    assert 0.3 < forecast < 1.2


def test_a_model_whose_inputs_touch_a_gap_returns_nan_not_a_guess() -> None:
    history = constant_vol_returns(0.5, 3000)
    history[-5] = np.nan

    assert math.isnan(NaiveLast().predict(history, 24))
    assert math.isnan(TrailingMean(720).predict(history, 24))
    assert math.isnan(Ewma().predict(history, 24))
    assert math.isnan(Har().predict(history, 24))
    assert NaiveLast().predict(history[:-6], 24) > 0  # before the gap it is fine


def test_forecasts_never_depend_on_data_after_the_origin() -> None:
    n, cutoff = 6000, 4500
    base = simulate_garch(n, seed=7)
    poisoned = base.copy()
    poisoned[cutoff + 1 :] = 0.5 * np.sign(np.sin(np.arange(n - cutoff - 1)))  # absurd future

    def run(returns: FloatArray) -> dict[tuple[int, str], float]:
        benchmark = np.full(n, 0.6)
        points = rolling_origin(
            returns,
            known_at(n),
            default_models({"implied": benchmark}),
            horizon=24,
            first_origin=3000,
            step=24,
            refit_every=30,
        )
        return {
            (p.origin_us, p.model): p.forecast for p in points if p.origin_us <= known_at(n)[cutoff]
        }

    clean, dirty = run(base), run(poisoned)

    assert len(clean) > 50 * 8 and clean == dirty  # identical forecasts, to the last digit


class CountingModel:
    name = "counting"

    def __init__(self) -> None:
        self.fits: list[int] = []

    def describe(self) -> dict[str, object]:
        return {}

    def fit(self, history: FloatArray, horizon: int) -> None:
        self.fits.append(len(history))

    def predict(self, history: FloatArray, horizon: int) -> float:
        return 0.5


def test_origins_are_spaced_by_the_step_refits_follow_the_schedule_and_targets_align() -> None:
    returns = constant_vol_returns(0.5, 400)
    model = CountingModel()

    points = rolling_origin(
        returns, known_at(400), [model], horizon=24, first_origin=100, step=48, refit_every=3
    )

    origins = [100 + 48 * k for k in range((400 - 24 - 100 + 47) // 48)]
    assert [p.origin_us for p in points] == [int(known_at(400)[o]) for o in origins]
    assert model.fits == [o + 1 for o in origins[::3]]
    realized = forward_realized_vol(returns, 24, 3600)
    assert [p.realized for p in points] == [float(realized[o]) for o in origins]


def test_an_origin_is_dropped_for_every_model_when_any_model_cannot_forecast() -> None:
    returns = constant_vol_returns(0.5, 4000)
    returns[2100] = np.nan  # a hole that touches the trailing windows of the next origins

    points = rolling_origin(
        returns,
        known_at(4000),
        [NaiveLast(), Ewma()],
        horizon=24,
        first_origin=2000,
        step=24,
        refit_every=10,
    )

    by_model: dict[str, set[int]] = {}
    for p in points:
        by_model.setdefault(p.model, set()).add(p.origin_us)
    assert by_model["naive"] == by_model["ewma_riskmetrics"] and by_model["naive"]
    gap_origin = int(known_at(4000)[2100])
    assert gap_origin not in by_model["naive"]


def test_metrics_match_hand_computed_values() -> None:
    realized = np.array([0.5, 0.5, 0.5])

    assert qlike(realized, realized).tolist() == [0.0, 0.0, 0.0]
    doubled = qlike(2 * realized, realized)
    assert doubled[0] == pytest.approx(0.25 - math.log(0.25) - 1)  # (1/2)^2 variance ratio
    x = np.array([0.3, 0.4, 0.5, 0.6])
    intercept, slope, r2 = mincer_zarnowitz(x, np.sqrt(0.1 + 2.0 * x**2))
    assert (intercept, slope, r2) == pytest.approx((0.1, 2.0, 1.0))
    assert all(math.isnan(v) for v in mincer_zarnowitz(np.array([0.5, 0.5, 0.5]), realized))


def test_bootstrap_is_reproducible_and_brackets_the_mean() -> None:
    gain = np.random.default_rng(0).normal(0.02, 0.05, 200)

    first, second = paired_bootstrap(gain, seed=3), paired_bootstrap(gain, seed=3)

    assert first == second
    mean, low, high = first
    assert low < mean < high and low > 0  # a clear, positive gain
    assert paired_bootstrap(gain, seed=4) != first


def test_scores_label_models_against_the_naive_baseline() -> None:
    returns = simulate_garch(14_000, seed=11)
    models = default_models()

    points = rolling_origin(
        returns,
        known_at(len(returns)),
        models,
        horizon=24,
        first_origin=6000,
        step=24,
        refit_every=30,
    )
    scores = {s.model: s for s in score_points(points, models)}

    assert scores["naive"].verdict == "baseline" and scores["naive"].gain_vs_naive is None
    # Data generated by a GARCH process: the matching model must clearly beat persistence.
    assert scores["garch"].verdict == "better than naive"
    assert scores["garch"].gain_low is not None and scores["garch"].gain_low > 0
    assert scores["garch"].qlike < scores["naive"].qlike
    assert all(s.n == scores["naive"].n for s in scores.values())


def test_evaluate_builds_a_json_report_with_hashes_and_the_test_window() -> None:
    returns = simulate_garch(9000, seed=13)

    report = evaluate(
        returns,
        known_at(len(returns)),
        series="TEST",
        horizons=[24, 168],
        test_start_index=6000,
        model_factory=lambda: [
            NaiveLast(),
            Ewma(),
            ExternalForecast("implied", np.full(9000, 0.5)),
        ],
    )

    assert [h.horizon_hours for h in report.horizons] == [24, 168]
    assert report.horizons[0].origins > report.horizons[1].origins > 10
    assert {s.model for s in report.horizons[0].scores} == {"naive", "ewma_riskmetrics", "implied"}
    assert all(len(s.params_hash) == 12 for s in report.horizons[0].scores)
    assert report.window_start < report.window_end and len(report.data_hash) == 16
    assert (
        "better than naive" in report.model_dump_json()
        or "no clear difference" in report.model_dump_json()
    )
