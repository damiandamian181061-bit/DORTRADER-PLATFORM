"""Out-of-sample evaluation of volatility forecasts.

Rolling origin only: at each origin a model sees the returns up to that candle's close and nothing
later, and is scored against the realized volatility of the following `horizon` hours. Origins are
spaced one horizon apart, so the scored target windows do not overlap. Every model is scored on
exactly the same origins. A model counts as better than the naive baseline only if the
bootstrap interval of its QLIKE gain excludes zero.
"""

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import numpy.typing as npt

from dortrader.domain import FrozenModel, UtcDateTime
from dortrader.forecasting.models import (
    HOURS_PER_YEAR,
    Ewma,
    ExternalForecast,
    FloatArray,
    ForecastModel,
    Garch,
    Har,
    NaiveLast,
    TrailingMean,
)
from dortrader.volatility.realized import US, forward_realized_vol

HOUR_SECONDS = 3600
BASELINE = "naive"
REFIT_HOURS = 720


@dataclass(frozen=True)
class ForecastPoint:
    origin_us: int  # when the forecast was made (close of the last candle it saw)
    model: str
    forecast: float
    realized: float


def default_models(benchmarks: dict[str, FloatArray] | None = None) -> list[ForecastModel]:
    models: list[ForecastModel] = [
        NaiveLast(),
        TrailingMean(720),
        Ewma(),
        Ewma(fitted=True),
        Garch(),
        Garch(asymmetric=True),
        Har(),
    ]
    models.extend(ExternalForecast(n, v) for n, v in (benchmarks or {}).items())
    return models


def rolling_origin(
    returns: FloatArray,
    known_at_us: npt.NDArray[np.int64],
    models: Sequence[ForecastModel],
    *,
    horizon: int,
    first_origin: int,
    step: int,
    refit_every: int,
) -> list[ForecastPoint]:
    """Forecast at origins first_origin, first_origin + step, ... for every model. An origin is
    kept only if the target and every model's forecast are finite."""
    realized = forward_realized_vol(returns, horizon, HOUR_SECONDS)
    points: list[ForecastPoint] = []
    for k, origin in enumerate(range(first_origin, len(returns) - horizon, step)):
        history = returns[: origin + 1]
        if k % refit_every == 0:
            for model in models:
                model.fit(history, horizon)
        target = realized[origin]
        forecasts = {m.name: m.predict(history, horizon) for m in models}
        if not np.isfinite(target) or not all(np.isfinite(f) for f in forecasts.values()):
            continue
        points.extend(
            ForecastPoint(int(known_at_us[origin]), name, f, float(target))
            for name, f in forecasts.items()
        )
    return points


def qlike(forecast_vol: FloatArray, realized_vol: FloatArray) -> FloatArray:
    """Per-origin QLIKE on variances: r/h - ln(r/h) - 1, zero when the forecast is exact."""
    ratio = (realized_vol / forecast_vol) ** 2
    return np.asarray(ratio - np.log(ratio) - 1.0, dtype=np.float64)


def mincer_zarnowitz(
    forecast_vol: FloatArray, realized_vol: FloatArray
) -> tuple[float, float, float]:
    """Regress realized variance on forecast variance: (intercept, slope, R squared). A calibrated
    forecast has intercept near 0 and slope near 1."""
    x, y = forecast_vol**2, realized_vol**2
    if len(x) < 3 or np.ptp(x) == 0:
        return float("nan"), float("nan"), float("nan")
    slope, intercept = np.polyfit(x, y, 1)
    fitted = intercept + slope * x
    total = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - float(np.sum((y - fitted) ** 2)) / total if total > 0 else float("nan")
    return float(intercept), float(slope), r2


def paired_bootstrap(
    gain: FloatArray, *, resamples: int = 2000, seed: int = 0
) -> tuple[float, float, float]:
    """Mean of `gain` with a 95% percentile bootstrap interval (seeded, so reproducible)."""
    rng = np.random.default_rng(seed)
    draws = gain[rng.integers(0, len(gain), size=(resamples, len(gain)))].mean(axis=1)
    low, high = np.percentile(draws, [2.5, 97.5])
    return float(gain.mean()), float(low), float(high)


class ModelScore(FrozenModel):
    model: str
    params_hash: str
    n: int
    qlike: float
    mse_variance: float
    mae_vol: float
    bias_vol: float
    mz_intercept: float | None  # None when the forecast never varies, so there is no fit
    mz_slope: float | None
    mz_r2: float | None
    gain_vs_naive: float | None  # mean QLIKE reduction against the naive baseline
    gain_low: float | None
    gain_high: float | None
    verdict: str


class HorizonReport(FrozenModel):
    horizon_hours: int
    step_hours: int
    origins: int
    scores: tuple[ModelScore, ...]


class EvaluationReport(FrozenModel):
    series: str
    generated_at: UtcDateTime
    data_hash: str
    window_start: UtcDateTime
    window_end: UtcDateTime
    horizons: tuple[HorizonReport, ...]


def params_hash(model: ForecastModel) -> str:
    payload = json.dumps({"name": model.name, **model.describe()}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def _finite(value: float) -> float | None:
    return value if np.isfinite(value) else None


def _verdict(model: str, low: float | None, high: float | None) -> str:
    if model == BASELINE:
        return "baseline"
    if low is None or high is None:
        return "n/a"
    if low > 0:
        return "better than naive"
    if high < 0:
        return "worse than naive"
    return "no clear difference"


def score_points(
    points: Sequence[ForecastPoint], models: Sequence[ForecastModel]
) -> list[ModelScore]:
    by_model: dict[str, list[ForecastPoint]] = {m.name: [] for m in models}
    for point in points:
        by_model[point.model].append(point)
    arrays = {
        name: (
            np.array([p.forecast for p in pts], dtype=np.float64),
            np.array([p.realized for p in pts], dtype=np.float64),
        )
        for name, pts in by_model.items()
        if pts
    }
    baseline_loss = qlike(*arrays[BASELINE]) if BASELINE in arrays else None
    scores = []
    for model in models:
        if model.name not in arrays:
            continue
        forecast, realized = arrays[model.name]
        losses = qlike(forecast, realized)
        intercept, slope, r2 = mincer_zarnowitz(forecast, realized)
        gain = low = high = None
        if baseline_loss is not None and model.name != BASELINE:
            gain, low, high = paired_bootstrap(baseline_loss - losses)
        scores.append(
            ModelScore(
                model=model.name,
                params_hash=params_hash(model),
                n=len(forecast),
                qlike=float(losses.mean()),
                mse_variance=float(np.mean((forecast**2 - realized**2) ** 2)),
                mae_vol=float(np.mean(np.abs(forecast - realized))),
                bias_vol=float(np.mean(forecast - realized)),
                mz_intercept=_finite(intercept),
                mz_slope=_finite(slope),
                mz_r2=_finite(r2),
                gain_vs_naive=gain,
                gain_low=low,
                gain_high=high,
                verdict=_verdict(model.name, low, high),
            )
        )
    return scores


def evaluate(
    returns: FloatArray,
    known_at_us: npt.NDArray[np.int64],
    *,
    series: str,
    horizons: Sequence[int],
    test_start_index: int,
    model_factory: Callable[[], list[ForecastModel]] = default_models,
    generated_at: datetime | None = None,
) -> EvaluationReport:
    """Score every model at every horizon from `test_start_index` to the end of the data."""
    reports = []
    for horizon in horizons:
        models = model_factory()
        points = rolling_origin(
            returns,
            known_at_us,
            models,
            horizon=horizon,
            first_origin=test_start_index,
            step=horizon,
            refit_every=max(1, REFIT_HOURS // horizon),
        )
        origins = len({p.origin_us for p in points})
        reports.append(
            HorizonReport(
                horizon_hours=horizon,
                step_hours=horizon,
                origins=origins,
                scores=tuple(score_points(points, models)),
            )
        )
    data_hash = hashlib.sha256(returns.tobytes() + known_at_us.tobytes()).hexdigest()[:16]
    return EvaluationReport(
        series=series,
        generated_at=generated_at or datetime.now(UTC),
        data_hash=data_hash,
        window_start=datetime.fromtimestamp(known_at_us[test_start_index] / US, tz=UTC),
        window_end=datetime.fromtimestamp(known_at_us[-1] / US, tz=UTC),
        horizons=tuple(reports),
    )


__all__ = [
    "BASELINE",
    "HOURS_PER_YEAR",
    "EvaluationReport",
    "ForecastPoint",
    "HorizonReport",
    "ModelScore",
    "default_models",
    "evaluate",
    "mincer_zarnowitz",
    "paired_bootstrap",
    "params_hash",
    "qlike",
    "rolling_origin",
    "score_points",
]
