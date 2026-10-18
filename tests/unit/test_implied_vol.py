import itertools
import math
import time

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from dortrader.pricing import (
    IVStatus,
    greeks,
    implied_vol,
    implied_vol_interval,
    implied_vol_scalar,
    price,
)

F = 81_000.0


def f(x: object) -> float:
    return float(np.asarray(x))


def solver_tolerance(p: float, vega_unit: float) -> float:
    return 1e-9 + 20 * 1e-12 * max(1.0, p) / vega_unit


def test_round_trip_over_a_wide_grid() -> None:
    cases = list(
        itertools.product(
            [0.4, 0.8, 0.95, 1.0, 1.05, 1.25, 2.5],  # K / F
            [1 / 24 / 365, 1 / 365, 7 / 365, 0.25, 2.0],  # T
            [0.05, 0.3, 0.8, 2.0, 4.0],  # sigma
            [True, False],
            [0.0, 0.04],
        )
    )
    m, t, s, call, r = (np.array(col) for col in zip(*cases, strict=True))
    K = F * m
    p = np.asarray(price(F, K, t, s, call, r))

    result = implied_vol(p, F, K, t, call, r)

    vega_unit = np.asarray(greeks(F, K, t, s, call, r).vega) * 100
    ok = result.ok
    # The only unsolved cases are ones whose time value is below 1e-7 USDC: nothing to invert.
    intrinsic = np.exp(-r * t) * np.maximum(np.where(call, F - K, K - F), 0.0)
    assert set(np.asarray(result.status)[~ok].tolist()) <= {IVStatus.AT_INTRINSIC}
    assert (p - intrinsic)[~ok].max() <= 1e-7
    assert ok.sum() > 450
    tolerance = 1e-9 + 20 * 1e-12 * np.maximum(1.0, p) / np.maximum(vega_unit, 1e-300)
    well_conditioned = ok & (vega_unit > 1e-3)
    assert (np.abs(result.value - s)[well_conditioned] <= tolerance[well_conditioned]).all()
    repriced = np.asarray(price(F, K, t, result.value, call, r))
    assert np.allclose(repriced[ok], p[ok], rtol=1e-9, atol=1e-9)


@pytest.mark.property
@given(
    m=st.floats(0.3, 3.0),
    T=st.floats(1e-4, 3.0),
    s=st.floats(0.03, 3.0),
    is_call=st.booleans(),
    r=st.floats(-0.02, 0.1),
)
def test_round_trip_property(m: float, T: float, s: float, is_call: bool, r: float) -> None:
    K = F * m
    p = f(price(F, K, T, s, is_call, r))
    vega_unit = f(greeks(F, K, T, s, is_call, r).vega) * 100

    value, status = implied_vol_scalar(p, F, K, T, is_call, r)

    if status is IVStatus.OK and vega_unit > 1e-3:
        assert abs(value - s) <= solver_tolerance(p, vega_unit)
    else:
        assert status in (IVStatus.AT_INTRINSIC, IVStatus.OK, IVStatus.ABOVE_UPPER_BOUND)


def test_price_below_intrinsic_is_reported_not_solved() -> None:
    value, status = implied_vol_scalar(1.0, F, 70_000.0, 0.1, True)  # intrinsic is 11,000

    assert status is IVStatus.BELOW_INTRINSIC and math.isnan(value)


def test_price_equal_to_intrinsic_is_indeterminate_with_zero_value() -> None:
    value, status = implied_vol_scalar(11_000.0, F, 70_000.0, 1e-9, True)

    assert status is IVStatus.AT_INTRINSIC and value == 0.0


def test_price_at_or_above_the_upper_bound_is_reported() -> None:
    assert implied_vol_scalar(F, F, K=F, T=0.1, is_call=True)[1] is IVStatus.ABOVE_UPPER_BOUND
    assert implied_vol_scalar(F + 1, F, F, 0.1, True)[1] is IVStatus.ABOVE_UPPER_BOUND
    assert implied_vol_scalar(F, F, F, 0.1, False)[1] is IVStatus.ABOVE_UPPER_BOUND  # put cap is K


def test_a_price_needing_more_than_2000_percent_vol_has_no_bracket() -> None:
    value, status = implied_vol_scalar(50.0, 100.0, 100.0, 1e-4, True)

    assert status is IVStatus.NO_BRACKET and math.isnan(value)


@pytest.mark.parametrize(
    "args",
    [
        (float("nan"), F, F, 0.1),
        (-1.0, F, F, 0.1),
        (100.0, 0.0, F, 0.1),
        (100.0, F, -5.0, 0.1),
        (100.0, F, F, 0.0),
        (100.0, F, F, -1.0),
        (float("inf"), F, F, 0.1),
    ],
)
def test_invalid_inputs_are_reported(args: tuple[float, float, float, float]) -> None:
    value, status = implied_vol_scalar(*args, is_call=True)

    assert status is IVStatus.INVALID_INPUT and math.isnan(value)


def test_a_mixed_chain_gets_one_status_per_contract() -> None:
    K = np.array([80_000.0, 80_000.0, 80_000.0, 80_000.0])
    sigma = 0.4
    good = f(price(F, 80_000.0, 0.1, sigma, True))
    prices = np.array([good, 1.0, float("nan"), F])

    result = implied_vol(prices, F, K, 0.1, True)

    assert result.statuses() == [
        IVStatus.OK,
        IVStatus.BELOW_INTRINSIC,
        IVStatus.INVALID_INPUT,
        IVStatus.ABOVE_UPPER_BOUND,
    ]
    assert result.value[0] == pytest.approx(sigma, abs=1e-9)
    assert result.value.shape == (4,)


def test_vectorized_solve_equals_one_at_a_time() -> None:
    K = F * np.linspace(0.7, 1.4, 40)
    p = np.asarray(price(F, K, 0.05, 0.5, K > F))

    together = implied_vol(p, F, K, 0.05, K > F)
    one_by_one = [
        implied_vol_scalar(float(pi), F, float(ki), 0.05, bool(ki > F))[0]
        for pi, ki in zip(p, K, strict=True)
    ]

    assert np.allclose(together.value, one_by_one, rtol=0, atol=1e-12, equal_nan=True)


def test_shapes_are_preserved() -> None:
    K = np.array([[79_000.0, 80_000.0], [81_000.0, 82_000.0]])
    p = np.asarray(price(F, K, 0.1, 0.4, True))

    result = implied_vol(p, F, K, 0.1, True)

    assert result.value.shape == (2, 2) and result.status.shape == (2, 2)
    assert np.allclose(result.value, 0.4, atol=1e-9)


def test_half_tick_interval_contains_the_true_vol_and_widens_far_otm() -> None:
    near = f(price(F, 81_000.0, 0.02, 0.4, True))
    far = f(price(F, 95_000.0, 0.02, 0.4, True))  # priced a few ticks above zero

    interval_near = implied_vol_interval(near, 2.5, F, 81_000.0, 0.02, True)
    interval_far = implied_vol_interval(far, 2.5, F, 95_000.0, 0.02, True)

    assert interval_near.low < 0.4 < interval_near.high
    assert interval_far.low < 0.4 < interval_far.high
    assert (interval_far.high - interval_far.low) > 5 * (interval_near.high - interval_near.low)


def test_interval_edges_report_zero_and_infinity_at_the_bounds() -> None:
    at_floor = implied_vol_interval(1.0, 5.0, F, 70_000.0, 0.1, True)  # tick covers the whole gap
    at_cap = implied_vol_interval(F - 1.0, 5.0, F, F, 0.1, True)

    assert at_floor.low == 0.0
    assert at_cap.high == math.inf


def test_solving_a_full_chain_is_fast_enough() -> None:
    K = F * np.linspace(0.6, 1.6, 600)
    p = np.asarray(price(F, K, 0.05, 0.5, K > F))

    started = time.perf_counter()
    result = implied_vol(p, F, K, 0.05, K > F)
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0  # generous ceiling; typically a few milliseconds
    assert result.ok.mean() > 0.9
