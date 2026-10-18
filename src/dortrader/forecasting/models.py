"""Volatility forecast baselines.

Every model forecasts the annualized realized volatility over the next `horizon` hours, from a
history of hourly log returns that ends at the forecast origin (the close of the last candle).
Nothing after the origin is ever passed in. A history may contain NaN where candles are missing;
a model whose inputs touch a NaN returns NaN instead of guessing, and the harness skips that origin
for every model.

Hourly returns are assumed, so one year is 365 * 24 hours.
"""

from typing import Protocol

import numpy as np
import numpy.typing as npt
from arch import arch_model
from numpy.lib.stride_tricks import sliding_window_view
from scipy.signal import lfilter

FloatArray = npt.NDArray[np.float64]
HOURS_PER_YEAR = 365.0 * 24.0
DAILY_RISKMETRICS_LAMBDA = 0.94


class ForecastModel(Protocol):
    name: str

    def describe(self) -> dict[str, object]:
        """Settings that define the model, for hashing and for the report."""
        ...

    def fit(self, history: FloatArray, horizon: int) -> None:
        """(Re)estimate parameters from returns up to the origin. Models with none do nothing."""
        ...

    def predict(self, history: FloatArray, horizon: int) -> float:
        """Annualized vol over the next `horizon` hours, using only `history`."""
        ...


def _annualize(variance_per_hour: float) -> float:
    return float(np.sqrt(max(variance_per_hour, 0.0) * HOURS_PER_YEAR))


def _recent_mean_square(history: FloatArray, hours: int) -> float:
    """Mean squared hourly return over the last `hours`; NaN if short or touching a gap."""
    if len(history) < hours:
        return float("nan")
    return float(np.mean(history[-hours:] ** 2))


def _forward_mean_square(returns: FloatArray, horizon: int) -> FloatArray:
    """For origin i, the mean squared return over candles i+1 .. i+horizon (NaN if unavailable)."""
    out = np.full(len(returns), np.nan)
    if horizon < len(returns):
        out[: len(returns) - horizon] = sliding_window_view(returns[1:] ** 2, horizon).mean(axis=1)
    return out


def _qlike(realized_variance: FloatArray, forecast_variance: FloatArray) -> float:
    ratio = realized_variance / forecast_variance
    return float(np.mean(ratio - np.log(ratio) - 1.0))


class NaiveLast:
    """Persistence: the next `horizon` hours will look like the last `horizon` hours."""

    name = "naive"

    def describe(self) -> dict[str, object]:
        return {}

    def fit(self, history: FloatArray, horizon: int) -> None:
        return None

    def predict(self, history: FloatArray, horizon: int) -> float:
        return _annualize(_recent_mean_square(history, horizon))


class TrailingMean:
    """The long-run level: realized variance over the last `hours`, whatever the horizon."""

    def __init__(self, hours: int = 720) -> None:
        self.hours = hours
        self.name = f"trailing_{hours // 24}d"

    def describe(self) -> dict[str, object]:
        return {"hours": self.hours}

    def fit(self, history: FloatArray, horizon: int) -> None:
        return None

    def predict(self, history: FloatArray, horizon: int) -> float:
        return _annualize(_recent_mean_square(history, self.hours))


class Ewma:
    """Exponentially weighted variance of hourly returns. Its forecast for any horizon is the
    current level, since EWMA variance is a random walk. `fitted=True` picks lambda from a grid by
    QLIKE on the history's own past targets; otherwise lambda is RiskMetrics' daily 0.94 expressed
    per hour."""

    GRID = (0.990, 0.995, 0.997, 0.998, 0.999, 0.9995)
    MEMORY = 2000  # hours; a gap inside this window makes the forecast NaN

    def __init__(self, decay: float | None = None, *, fitted: bool = False) -> None:
        self.fitted = fitted
        self.decay = decay if decay is not None else DAILY_RISKMETRICS_LAMBDA ** (1 / 24)
        self.name = "ewma_fitted" if fitted else "ewma_riskmetrics"

    def describe(self) -> dict[str, object]:
        return {"decay": round(self.decay, 6), "fitted": self.fitted}

    @staticmethod
    def _levels(history: FloatArray, decay: float) -> FloatArray:
        squares = np.nan_to_num(history**2, nan=0.0)
        seed = float(np.mean(squares[: min(len(squares), 500)]))
        filtered = lfilter([1.0 - decay], [1.0, -decay], squares, zi=[decay * seed])[0]
        return np.asarray(filtered, dtype=np.float64)

    def fit(self, history: FloatArray, horizon: int) -> None:
        if not self.fitted:
            return
        target = _forward_mean_square(history, horizon)
        origins = np.arange(self.MEMORY, len(history) - horizon, 24)
        origins = origins[np.isfinite(target[origins])]
        if len(origins) < 50:
            return
        losses = {
            decay: _qlike(target[origins], self._levels(history, decay)[origins])
            for decay in self.GRID
        }
        self.decay = min(losses, key=losses.__getitem__)

    def predict(self, history: FloatArray, horizon: int) -> float:
        if len(history) < self.MEMORY or not np.isfinite(history[-self.MEMORY :]).all():
            return float("nan")
        return _annualize(float(self._levels(history, self.decay)[-1]))


class Garch:
    """GARCH(1,1), or GJR-GARCH(1,1) with `asymmetric=True`, on zero-mean hourly returns.
    Parameters are estimated on the last `train_hours` at each refit; between refits the filter
    is rerun on fresh data with the parameters held fixed. The multi-step forecast is analytic."""

    def __init__(self, *, asymmetric: bool = False, train_hours: int = 17_520) -> None:
        self.asymmetric = asymmetric
        self.train_hours = train_hours
        self.name = "gjr_garch" if asymmetric else "garch"
        self._params: object = None

    def describe(self) -> dict[str, object]:
        return {"asymmetric": self.asymmetric, "train_hours": self.train_hours}

    @property
    def parameters(self) -> dict[str, float]:
        """Fitted parameters in percent-return units (omega, alpha[1], beta[1], gamma[1])."""
        return {} if self._params is None else {str(k): float(v) for k, v in self._params.items()}  # type: ignore[attr-defined]

    def _model(self, history: FloatArray):  # type: ignore[no-untyped-def]
        data = history[-self.train_hours :]
        data = data[np.isfinite(data)] * 100.0  # percent returns keep the optimizer well scaled
        return arch_model(
            data, mean="Zero", vol="GARCH", p=1, o=int(self.asymmetric), q=1, rescale=False
        )

    def fit(self, history: FloatArray, horizon: int) -> None:
        result = self._model(history).fit(disp="off", show_warning=False)
        self._params = result.params

    def predict(self, history: FloatArray, horizon: int) -> float:
        if self._params is None:
            self.fit(history, horizon)
        result = self._model(history).fix(self._params)
        variance = result.forecast(horizon=horizon, reindex=False).variance.to_numpy()[-1]
        return _annualize(float(variance.sum()) / horizon / 1e4)


class Har:
    """HAR-RV: next-period hourly variance regressed on the last day's, week's and month's mean
    squared return (level OLS with an intercept), fitted directly for the horizon. Training
    pairs are origins whose target finished before the current origin, so nothing is peeked at."""

    LAGS = (24, 168, 720)

    def __init__(self) -> None:
        self.name = "har"
        self._coefficients: FloatArray | None = None

    def describe(self) -> dict[str, object]:
        return {"lags_hours": list(self.LAGS)}

    def _features(self, returns: FloatArray) -> FloatArray:
        squares = returns**2
        columns = []
        for lag in self.LAGS:
            means = np.full(len(returns), np.nan)
            means[lag - 1 :] = sliding_window_view(squares, lag).mean(axis=1)
            columns.append(means)
        return np.column_stack(columns)

    def fit(self, history: FloatArray, horizon: int) -> None:
        features = self._features(history)
        target = _forward_mean_square(history, horizon)
        origins = np.arange(self.LAGS[-1], len(history) - horizon, 24)
        design = np.column_stack([np.ones(len(origins)), features[origins]])
        outcome = target[origins]
        keep = np.isfinite(design).all(axis=1) & np.isfinite(outcome)
        if keep.sum() < 100:
            self._coefficients = None
            return
        self._coefficients, *_ = np.linalg.lstsq(design[keep], outcome[keep], rcond=None)

    def predict(self, history: FloatArray, horizon: int) -> float:
        if self._coefficients is None:
            self.fit(history, horizon)
        if self._coefficients is None or len(history) < self.LAGS[-1]:
            return float("nan")
        row = np.array([_recent_mean_square(history, lag) for lag in self.LAGS])
        if not np.isfinite(row).all():
            return float("nan")
        forecast = float(self._coefficients[0] + row @ self._coefficients[1:])
        return _annualize(max(forecast, 1e-12))


class ExternalForecast:
    """A benchmark that is not a model of ours, such as an implied volatility index. `values` is
    aligned to the returns grid and holds a decimal annualized vol known at each candle's close."""

    def __init__(self, name: str, values: FloatArray) -> None:
        self.name = name
        self._values = values

    def describe(self) -> dict[str, object]:
        return {"points": int(np.isfinite(self._values).sum())}

    def fit(self, history: FloatArray, horizon: int) -> None:
        return None

    def predict(self, history: FloatArray, horizon: int) -> float:
        return float(self._values[len(history) - 1])
