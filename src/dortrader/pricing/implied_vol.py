"""Implied volatility under Black-76, vectorized over whole chains.

Safeguarded Newton: every step keeps a [lo, hi] bracket on sigma (price rises with sigma), and
falls back to bisection when Newton leaves the bracket or vega is negligible. The solver never
returns a silent number: every element carries a status, and only OK elements are trustworthy.
"""

from dataclasses import dataclass
from enum import IntEnum

import numpy as np
import numpy.typing as npt
from numpy.typing import ArrayLike

from dortrader.pricing.black76 import FloatArray, price_and_vega_unit

SIGMA_MIN = 1e-8
SIGMA_MAX = 20.0  # 2000% vol; anything needing more is reported as NO_BRACKET


class IVStatus(IntEnum):
    OK = 0
    INVALID_INPUT = 1  # non-finite, F or K not positive, T not positive, negative price
    BELOW_INTRINSIC = 2  # price below discounted intrinsic value: no volatility explains it
    AT_INTRINSIC = 3  # price equals intrinsic: vol indistinguishable from zero (value 0.0)
    ABOVE_UPPER_BOUND = 4  # price at or above df*F (call) / df*K (put)
    NO_BRACKET = 5  # would need sigma above SIGMA_MAX
    NOT_CONVERGED = 6


@dataclass(frozen=True)
class IVResult:
    value: FloatArray  # NaN unless OK (0.0 for AT_INTRINSIC)
    status: npt.NDArray[np.int8]
    iterations: npt.NDArray[np.int32]
    residual: FloatArray  # |model price - target price| at the returned sigma

    def statuses(self) -> list[IVStatus]:
        return [IVStatus(int(code)) for code in np.ravel(self.status)]

    @property
    def ok(self) -> npt.NDArray[np.bool_]:
        return np.asarray(self.status == IVStatus.OK, dtype=np.bool_)


@dataclass(frozen=True)
class IVInterval:
    low: FloatArray  # 0.0 when price - half_tick reaches intrinsic
    high: FloatArray  # inf when price + half_tick reaches the upper bound


def implied_vol(
    price: ArrayLike,
    F: ArrayLike,
    K: ArrayLike,
    T: ArrayLike,
    is_call: ArrayLike,
    r: ArrayLike = 0.0,
    *,
    tol: float = 1e-12,
    max_iter: int = 100,
) -> IVResult:
    p, f, k, t, rate = np.broadcast_arrays(
        *(np.asarray(x, dtype=np.float64) for x in (price, F, K, T, r))
    )
    call = np.broadcast_to(np.asarray(is_call, dtype=bool), p.shape)
    shape = p.shape
    p, f, k, t, rate, call = (np.ravel(x) for x in (p, f, k, t, rate, call))

    with np.errstate(invalid="ignore"):
        valid = (
            np.isfinite(p) & np.isfinite(f) & np.isfinite(k) & np.isfinite(t) & np.isfinite(rate)
            & (f > 0) & (k > 0) & (t > 0) & (p >= 0)
        )  # fmt: skip
    df = np.exp(-rate * np.where(valid, t, 0.0))
    intrinsic = df * np.maximum(np.where(call, f - k, k - f), 0.0)
    upper = df * np.where(call, f, k)
    eps = 1e-12 * np.maximum(f, k)
    with np.errstate(invalid="ignore"):
        below = valid & (p < intrinsic - eps)
        at = valid & ~below & (p <= intrinsic + eps)
        above = valid & ~below & ~at & (p >= upper - eps)
    active = valid & ~below & ~at & ~above

    status = np.full(p.shape, IVStatus.OK, dtype=np.int8)
    status[~valid] = IVStatus.INVALID_INPUT
    status[below] = IVStatus.BELOW_INTRINSIC
    status[at] = IVStatus.AT_INTRINSIC
    status[above] = IVStatus.ABOVE_UPPER_BOUND

    safe_f = np.where(valid, f, 1.0)
    safe_t = np.where(valid, t, 1.0)
    lo = np.full(p.shape, SIGMA_MIN)
    hi = np.full(p.shape, SIGMA_MAX)
    sigma = np.clip(
        np.sqrt(2.0 * np.pi / safe_t) * np.where(valid, p, 0.0) / (df * safe_f), 0.05, 5.0
    )

    price_at_hi, _ = price_and_vega_unit(safe_f, np.where(valid, k, 1.0), safe_t, hi, call, rate)
    no_bracket = active & ~(price_at_hi > p)
    status[no_bracket] = IVStatus.NO_BRACKET
    active &= ~no_bracket

    iterations = np.zeros(p.shape, dtype=np.int32)
    converged = ~active
    residual = np.zeros(p.shape)
    atol = tol * np.maximum(1.0, np.where(valid, p, 1.0))
    kk = np.where(valid, k, 1.0)

    for step in range(1, max_iter + 1):
        running = ~converged
        if not running.any():
            break
        model, vega = price_and_vega_unit(safe_f, kk, safe_t, sigma, call, rate)
        diff = model - p
        done = running & ((np.abs(diff) <= atol) | ((hi - lo) <= 1e-15 * np.maximum(1.0, hi)))
        residual = np.where(done, np.abs(diff), residual)
        iterations = np.where(running, step, iterations)
        converged |= done
        running &= ~done
        hi = np.where(running & (diff > 0), sigma, hi)
        lo = np.where(running & (diff < 0), sigma, lo)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            newton = sigma - diff / vega
        usable = np.isfinite(newton) & (newton > lo) & (newton < hi) & (vega > 1e-300)
        sigma = np.where(running, np.where(usable, newton, 0.5 * (lo + hi)), sigma)

    status[active & ~converged] = IVStatus.NOT_CONVERGED
    value = np.where(status == IVStatus.OK, sigma, np.nan)
    value = np.where(status == IVStatus.AT_INTRINSIC, 0.0, value)
    return IVResult(
        value=value.reshape(shape),
        status=status.reshape(shape),
        iterations=iterations.reshape(shape),
        residual=np.where(status == IVStatus.OK, residual, np.nan).reshape(shape),
    )


def implied_vol_scalar(
    price: float, F: float, K: float, T: float, is_call: bool, r: float = 0.0
) -> tuple[float, IVStatus]:
    result = implied_vol(price, F, K, T, is_call, r)
    return float(result.value), IVStatus(int(result.status))


def implied_vol_interval(
    price: ArrayLike,
    half_tick: ArrayLike,
    F: ArrayLike,
    K: ArrayLike,
    T: ArrayLike,
    is_call: ArrayLike,
    r: ArrayLike = 0.0,
) -> IVInterval:
    """IV range consistent with the quoted price being off by up to half a tick.

    Deep out-of-the-money options sit a few ticks above zero, so their IV is only known to a
    wide interval; weight them accordingly instead of trusting the point estimate.
    """
    p = np.asarray(price, dtype=np.float64)
    h = np.asarray(half_tick, dtype=np.float64)
    low = implied_vol(np.maximum(p - h, 0.0), F, K, T, is_call, r)
    high = implied_vol(p + h, F, K, T, is_call, r)
    low_status = low.status
    high_status = high.status
    low_value = np.where(
        (low_status == IVStatus.BELOW_INTRINSIC) | (low_status == IVStatus.AT_INTRINSIC),
        0.0,
        low.value,
    )
    high_value = np.where(
        (high_status == IVStatus.ABOVE_UPPER_BOUND) | (high_status == IVStatus.NO_BRACKET),
        np.inf,
        high.value,
    )
    return IVInterval(low=low_value, high=high_value)
