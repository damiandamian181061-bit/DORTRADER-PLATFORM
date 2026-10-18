"""Black-76 price and Greeks, vectorized. Units and conventions: see conventions.py.

Invalid inputs (non-finite, F or K not positive, negative T or sigma) give NaN, never a
plausible-looking number. T = 0 or sigma = 0 is valid and returns intrinsic value.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
from numpy.typing import ArrayLike
from scipy.special import ndtr

from dortrader.pricing.conventions import DAYS_PER_YEAR

FloatArray = npt.NDArray[np.float64]
FloatOrArray = float | FloatArray

_INV_SQRT_2PI = 1.0 / np.sqrt(2.0 * np.pi)


@dataclass(frozen=True)
class Greeks:
    price: FloatOrArray
    delta: FloatOrArray
    gamma: FloatOrArray
    vega: FloatOrArray
    theta: FloatOrArray
    vanna: FloatOrArray
    volga: FloatOrArray

    def scaled(self, factor: float) -> "Greeks":
        """Every quantity is linear in position size: contract_size * quantity."""
        return Greeks(
            price=self.price * factor,
            delta=self.delta * factor,
            gamma=self.gamma * factor,
            vega=self.vega * factor,
            theta=self.theta * factor,
            vanna=self.vanna * factor,
            volga=self.volga * factor,
        )


@dataclass(frozen=True)
class _Terms:
    F: FloatArray
    K: FloatArray
    T: FloatArray
    sigma: FloatArray
    r: FloatArray
    phi: FloatArray  # +1 call, -1 put
    df: FloatArray
    valid: npt.NDArray[np.bool_]
    live: npt.NDArray[np.bool_]  # valid and T > 0 and sigma > 0
    sd: FloatArray  # sigma * sqrt(T), 1.0 where not live (keeps divisions finite)
    d1: FloatArray
    d2: FloatArray
    pdf_d1: FloatArray


def _terms(
    F: ArrayLike, K: ArrayLike, T: ArrayLike, sigma: ArrayLike, is_call: ArrayLike, r: ArrayLike
) -> _Terms:
    f, k, t, s, rate = (np.asarray(x, dtype=np.float64) for x in (F, K, T, sigma, r))
    f, k, t, s, rate = np.broadcast_arrays(f, k, t, s, rate)
    call = np.broadcast_to(np.asarray(is_call, dtype=bool), f.shape)
    finite = np.isfinite(f) & np.isfinite(k) & np.isfinite(t) & np.isfinite(s) & np.isfinite(rate)
    with np.errstate(invalid="ignore"):
        valid = finite & (f > 0) & (k > 0) & (t >= 0) & (s >= 0)
    live = valid & (t > 0) & (s > 0)
    safe_t = np.where(live, t, 1.0)
    sd = np.where(live, s * np.sqrt(safe_t), 1.0)
    log_moneyness = np.log(np.where(live, f / np.where(live, k, 1.0), 1.0))
    d1 = log_moneyness / sd + 0.5 * sd
    d2 = d1 - sd
    return _Terms(
        F=f,
        K=k,
        T=t,
        sigma=s,
        r=rate,
        phi=np.where(call, 1.0, -1.0),
        df=np.exp(-rate * np.where(valid, t, 0.0)),
        valid=valid,
        live=live,
        sd=sd,
        d1=d1,
        d2=d2,
        pdf_d1=_INV_SQRT_2PI * np.exp(-0.5 * d1 * d1),
    )


def _out(x: FloatArray, scalar: bool) -> Any:
    return float(x) if scalar and x.ndim == 0 else x


def _is_scalar(*values: ArrayLike) -> bool:
    return all(np.ndim(v) == 0 for v in values)


def _price(t: _Terms) -> FloatArray:
    live_price = t.df * t.phi * (t.F * ndtr(t.phi * t.d1) - t.K * ndtr(t.phi * t.d2))
    intrinsic = t.df * np.maximum(t.phi * (t.F - t.K), 0.0)
    return np.where(t.valid, np.where(t.live, live_price, intrinsic), np.nan)


def price(
    F: ArrayLike,
    K: ArrayLike,
    T: ArrayLike,
    sigma: ArrayLike,
    is_call: ArrayLike,
    r: ArrayLike = 0.0,
) -> FloatOrArray:
    scalar = _is_scalar(F, K, T, sigma, is_call, r)
    return _out(_price(_terms(F, K, T, sigma, is_call, r)), scalar)  # type: ignore[no-any-return]


def price_and_vega_unit(
    F: ArrayLike,
    K: ArrayLike,
    T: ArrayLike,
    sigma: ArrayLike,
    is_call: ArrayLike,
    r: ArrayLike = 0.0,
) -> tuple[FloatArray, FloatArray]:
    """Price and d price / d sigma (per unit sigma), as arrays. For root-finders."""
    t = _terms(F, K, T, sigma, is_call, r)
    vega = t.df * t.F * t.pdf_d1 * np.sqrt(np.where(t.live, t.T, 0.0))
    return _price(t), np.where(t.valid, vega, np.nan)


def _greeks(
    F: ArrayLike,
    K: ArrayLike,
    T: ArrayLike,
    sigma: ArrayLike,
    is_call: ArrayLike,
    r: ArrayLike = 0.0,
) -> Greeks:
    scalar = _is_scalar(F, K, T, sigma, is_call, r)
    t = _terms(F, K, T, sigma, is_call, r)
    nan = np.nan
    live_price = _price(t)

    delta_live = t.df * t.phi * ndtr(t.phi * t.d1)
    delta_flat = t.df * np.where(
        t.phi > 0, np.where(t.F > t.K, 1.0, 0.0), np.where(t.F < t.K, -1.0, 0.0)
    )
    gamma = t.df * t.pdf_d1 / (t.F * t.sd)
    vega_unit = t.df * t.F * t.pdf_d1 * np.sqrt(np.where(t.live, t.T, 0.0))
    theta_year = t.r * live_price - t.df * t.F * t.pdf_d1 * t.sigma / (
        2.0 * np.sqrt(np.where(t.live, t.T, 1.0))
    )
    vanna_unit = -t.df * t.pdf_d1 * t.d2 / np.where(t.live, t.sigma, 1.0)
    volga_unit = vega_unit * t.d1 * t.d2 / np.where(t.live, t.sigma, 1.0)

    def pick(live_value: FloatArray, flat_value: float | FloatArray = 0.0) -> Any:
        masked = np.where(t.valid, np.where(t.live, live_value, flat_value), nan)
        return _out(masked, scalar)

    return Greeks(
        price=_out(live_price, scalar),
        delta=pick(delta_live, delta_flat),
        gamma=pick(gamma),
        vega=pick(vega_unit / 100.0),
        theta=pick(theta_year / DAYS_PER_YEAR),
        vanna=pick(vanna_unit / 100.0),
        volga=pick(volga_unit / 1.0e4),
    )


def greeks(
    F: ArrayLike,
    K: ArrayLike,
    T: ArrayLike,
    sigma: ArrayLike,
    is_call: ArrayLike,
    r: ArrayLike = 0.0,
) -> Greeks:
    # Invalid inputs divide by zero on the way to NaN; the NaN is the signal, not a warning.
    with np.errstate(all="ignore"):
        return _greeks(F, K, T, sigma, is_call, r)


def theta_deribit_style(theta_per_day: ArrayLike, price_: ArrayLike, T: ArrayLike) -> FloatOrArray:
    """Deribit's reported theta, for comparison only: decay over the next min(1 day, remaining
    life), capped at the option's value. Observed on live tickers 2026-10-18: with half a day left
    their theta is our per-day theta times 0.52, and deep out-of-the-money options show -mark.
    """
    th = np.asarray(theta_per_day, dtype=np.float64)
    p = np.asarray(price_, dtype=np.float64)
    days_left = np.asarray(T, dtype=np.float64) * DAYS_PER_YEAR
    out = np.maximum(th * np.minimum(1.0, days_left), -p)
    return _out(out, _is_scalar(theta_per_day, price_, T))  # type: ignore[no-any-return]
