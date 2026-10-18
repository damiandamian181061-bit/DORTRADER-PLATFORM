"""Realized-vol and forecast views for the API. Pure builders over candles, plus a small cache."""

import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from apps.api.schemas import (
    ForecastResponse,
    HorizonForecast,
    LastRealized,
    ModelForecast,
    RealizedSeries,
    RealizedVolResponse,
    VolPoint,
)
from dortrader.forecasting import Garch, default_models
from dortrader.forecasting.models import HOURS_PER_YEAR, FloatArray
from dortrader.storage.bars import BarStore
from dortrader.volatility.realized import US, Candles, Estimator, rolling_realized_vol

HOUR = 3600
HORIZONS = (24, 168, 720)
DVOL = "DVOL-BTC"

_candle_cache: dict[tuple[str, int, int], Candles] = {}
_forecast_cache: dict[tuple[str, int], ForecastResponse] = {}


class NoSeries(Exception):
    """The requested candles are not stored (or too few to compute anything)."""


def _moment(us: int) -> datetime:
    return datetime.fromtimestamp(us / US, tz=UTC)


def load_candles(store: BarStore, root: Path, instrument: str) -> Candles:
    """Hourly candles for `instrument`, rebuilt only when a newer candle has arrived."""
    bounds = store.bounds(instrument, HOUR)
    if bounds is None:
        raise NoSeries(f"no hourly candles stored for {instrument}; run the backfill")
    key = (f"{root}|{instrument}", HOUR, int(bounds[1].timestamp()))
    if key not in _candle_cache:
        bars = store.read(instrument, HOUR)
        _candle_cache.clear()  # keep one generation: candles are large
        _candle_cache[key] = Candles.from_bars(bars)
    return _candle_cache[key]


def _finite_or_none(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def build_realized_response(
    candles: Candles,
    dvol: Candles | None,
    *,
    instrument: str,
    estimators: list[Estimator],
    window_days: int,
    days: int,
) -> RealizedVolResponse:
    window = window_days * 24
    if window < 2 or window > len(candles.close):
        raise NoSeries(f"a {window_days}-day window needs at least {window} hourly candles")
    known = candles.known_at
    # One point per UTC midnight, in the last `days` days, where the value is known.
    at_midnight = (known % (86_400 * US)) == 0
    recent = known >= known[-1] - days * 86_400 * US
    chosen = np.flatnonzero(at_midnight & recent)
    series = []
    for estimator in estimators:
        vol = rolling_realized_vol(candles, estimator, window)
        series.append(
            RealizedSeries(
                estimator=estimator.value,
                points=[
                    VolPoint(time=_moment(int(known[i])), vol=_finite_or_none(vol[i]))
                    for i in chosen
                ],
            )
        )
    dvol_points: list[VolPoint] = []
    if dvol is not None:
        by_known = {
            int(k): float(c) / 100.0 for k, c in zip(dvol.known_at, dvol.close, strict=True)
        }
        dvol_points = [
            VolPoint(
                time=_moment(int(known[i])),
                vol=_finite_or_none(by_known.get(int(known[i]), math.nan)),
            )
            for i in chosen
        ]
    return RealizedVolResponse(
        instrument=instrument,
        window_days=window_days,
        estimators=[e.value for e in estimators],
        series=series,
        dvol=dvol_points,
    )


def _past_realized(returns: FloatArray, hours: int) -> float | None:
    if len(returns) < hours:
        return None
    return _finite_or_none(float(np.sqrt(np.mean(returns[-hours:] ** 2) * HOURS_PER_YEAR)))


def build_forecast_response(
    candles: Candles, dvol: Candles | None, *, instrument: str
) -> ForecastResponse:
    returns = candles.log_returns()
    if len(returns) < 720 + 24 * 100:
        raise NoSeries(
            "not enough history to fit the models (needs about 130 days of hourly candles)"
        )
    models = default_models()
    horizons = []
    for horizon in HORIZONS:
        forecasts = []
        for model in models:
            already_fitted = isinstance(model, Garch) and bool(model.parameters)
            if not already_fitted:
                model.fit(returns, horizon)  # GARCH parameters do not depend on the horizon
            vol = model.predict(returns, horizon)
            if math.isfinite(vol):
                forecasts.append(ModelForecast(model=model.name, vol=vol))
        horizons.append(HorizonForecast(horizon_hours=horizon, forecasts=forecasts))
    dvol_now = None
    if dvol is not None:
        by_known = dict(zip(dvol.known_at.tolist(), dvol.close.tolist(), strict=True))
        latest = by_known.get(int(candles.known_at[-1]))
        dvol_now = None if latest is None else float(latest) / 100.0
    return ForecastResponse(
        instrument=instrument,
        as_of=_moment(int(candles.known_at[-1])),
        last_realized=[
            LastRealized(window_hours=h, vol=_past_realized(returns, h)) for h in HORIZONS
        ],
        dvol=dvol_now,
        horizons=horizons,
    )


def cached_forecast(candles: Candles, dvol: Candles | None, *, instrument: str) -> ForecastResponse:
    key = (instrument, int(candles.known_at[-1]))
    if key not in _forecast_cache:
        _forecast_cache.clear()
        _forecast_cache[key] = build_forecast_response(candles, dvol, instrument=instrument)
    return _forecast_cache[key]
