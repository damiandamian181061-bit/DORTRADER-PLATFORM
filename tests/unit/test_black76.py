import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dortrader.pricing import greeks, price, theta_deribit_style, time_to_expiry_years

F, K, T, S = 81_000.0, 80_000.0, 30 / 365, 0.4

forwards = st.floats(1.0, 1e6)
strikes = st.floats(1.0, 1e6)
years = st.floats(1e-5, 3.0)
vols = st.floats(0.02, 3.0)
rates = st.floats(-0.05, 0.15)


def f(x: object) -> float:
    return float(np.asarray(x))


@pytest.mark.property
@given(F=forwards, K=strikes, T=years, s=vols, r=rates)
def test_put_call_parity(F: float, K: float, T: float, s: float, r: float) -> None:
    call, put = f(price(F, K, T, s, True, r)), f(price(F, K, T, s, False, r))

    assert math.isclose(call - put, math.exp(-r * T) * (F - K), rel_tol=1e-9, abs_tol=1e-9 * F)


@pytest.mark.property
@given(F=forwards, K=strikes, T=years, s=vols, is_call=st.booleans())
def test_price_never_decreases_with_volatility(
    F: float, K: float, T: float, s: float, is_call: bool
) -> None:
    assert f(price(F, K, T, s * 1.1, is_call)) >= f(price(F, K, T, s, is_call)) - 1e-9 * F


@pytest.mark.property
@given(F=forwards, K=strikes, T=years, s=vols)
def test_call_price_is_convex_in_strike(F: float, K: float, T: float, s: float) -> None:
    h = 0.01 * K
    fly = (
        f(price(F, K - h, T, s, True))
        - 2 * f(price(F, K, T, s, True))
        + f(price(F, K + h, T, s, True))
    )

    assert fly >= -1e-9 * F


@pytest.mark.property
@given(F=forwards, K=strikes, T=years, s=vols, r=rates)
def test_delta_bounds_and_nonnegative_gamma_and_vega(
    F: float, K: float, T: float, s: float, r: float
) -> None:
    df = math.exp(-r * T)
    call, put = greeks(F, K, T, s, True, r), greeks(F, K, T, s, False, r)

    assert -1e-12 <= f(call.delta) <= df + 1e-12
    assert -df - 1e-12 <= f(put.delta) <= 1e-12
    assert f(call.gamma) >= 0 and f(call.vega) >= 0
    assert f(call.gamma) == pytest.approx(f(put.gamma)) and f(call.vega) == pytest.approx(
        f(put.vega)
    )


@pytest.mark.property
@given(F=forwards, K=strikes, T=years, s=vols, lam=st.floats(0.1, 10.0), is_call=st.booleans())
def test_price_is_homogeneous_in_forward_and_strike(
    F: float, K: float, T: float, s: float, lam: float, is_call: bool
) -> None:
    scaled = f(price(lam * F, lam * K, T, s, is_call))

    assert math.isclose(
        scaled, lam * f(price(F, K, T, s, is_call)), rel_tol=1e-9, abs_tol=1e-9 * lam * F
    )


@pytest.mark.property
@given(F=forwards, K=strikes, T=years, s=vols)
def test_call_put_symmetry(F: float, K: float, T: float, s: float) -> None:
    # Swapping forward and strike turns a call into a put with the same price: C(F, K) = P(K, F).
    lhs = f(price(F, K, T, s, True))
    rhs = f(price(K, F, T, s, False))

    assert math.isclose(lhs, rhs, rel_tol=1e-8, abs_tol=1e-9 * max(F, K))


@pytest.mark.parametrize("is_call", [True, False])
@pytest.mark.parametrize("r", [0.0, 0.04])
def test_greeks_match_central_finite_differences(is_call: bool, r: float) -> None:
    g = greeks(F, K, T, S, is_call, r)

    def p(F_: float = F, T_: float = T, s_: float = S) -> float:
        return f(price(F_, K, T_, s_, is_call, r))

    h = F * 1e-5
    assert f(g.delta) == pytest.approx((p(F_=F + h) - p(F_=F - h)) / (2 * h), rel=1e-7)
    h = F * 1e-4
    assert f(g.gamma) == pytest.approx((p(F_=F + h) - 2 * p() + p(F_=F - h)) / h**2, rel=1e-5)
    h = 1e-5
    assert f(g.vega) == pytest.approx((p(s_=S + h) - p(s_=S - h)) / (2 * h) / 100, rel=1e-7)
    h = T * 1e-5
    assert f(g.theta) == pytest.approx(-(p(T_=T + h) - p(T_=T - h)) / (2 * h) / 365, rel=1e-6)

    def delta_at(s_: float) -> float:
        return f(greeks(F, K, T, s_, is_call, r).delta)

    def vega_at(s_: float) -> float:
        return f(greeks(F, K, T, s_, is_call, r).vega)

    h = 1e-5
    assert f(g.vanna) == pytest.approx(
        (delta_at(S + h) - delta_at(S - h)) / (2 * h) / 100, rel=1e-6
    )
    assert f(g.volga) == pytest.approx((vega_at(S + h) - vega_at(S - h)) / (2 * h) / 100, rel=1e-6)


@pytest.mark.parametrize("is_call", [True, False])
@pytest.mark.parametrize(
    ("F_", "K_", "T_", "s_"),
    [
        (81_000.0, 81_000.0 * 1e-6, 0.1, 0.4),  # absurdly deep in the money
        (81_000.0, 81_000.0 * 1e6, 0.1, 0.4),  # absurdly far out of the money
        (81_000.0, 81_000.0, 1e-12, 0.4),  # microseconds to expiry, at the money
        (81_000.0, 81_000.0, 0.1, 1e-8),  # almost no volatility
        (81_000.0, 81_000.0, 0.1, 50.0),  # 5000% volatility
        (81_000.0, 81_000.0, 100.0, 0.4),  # a century out
    ],
)
def test_extreme_inputs_give_finite_values_not_nan(
    is_call: bool, F_: float, K_: float, T_: float, s_: float
) -> None:
    g = greeks(F_, K_, T_, s_, is_call)

    for quantity in (g.price, g.delta, g.gamma, g.vega, g.theta, g.vanna, g.volga):
        assert math.isfinite(f(quantity))


@pytest.mark.parametrize("is_call", [True, False])
@pytest.mark.parametrize(("T_", "s_"), [(0.0, 0.4), (0.1, 0.0)])
def test_zero_time_or_volatility_returns_intrinsic_with_step_delta(
    is_call: bool, T_: float, s_: float
) -> None:
    itm, otm = (F - K, K - F) if is_call else (K - F, F - K)
    g_itm = greeks(F, K, T_, s_, is_call)
    g_otm = greeks(K, F, T_, s_, is_call)

    assert f(g_itm.price) == pytest.approx(max(itm, 0.0))
    assert f(g_otm.price) == pytest.approx(max(otm, 0.0))
    assert abs(f(g_itm.delta)) in (0.0, 1.0)
    assert f(g_itm.gamma) == 0 and f(g_itm.vega) == 0 and f(g_itm.theta) == 0


@pytest.mark.parametrize(
    "bad",
    [
        {"F": 0.0},
        {"F": -1.0},
        {"K": 0.0},
        {"T": -0.1},
        {"s": -0.2},
        {"F": float("nan")},
        {"K": float("inf")},
        {"T": float("nan")},
        {"s": float("inf")},
    ],
)
def test_invalid_inputs_are_nan_never_a_plausible_number(bad: dict[str, float]) -> None:
    args = {"F": F, "K": K, "T": T, "s": S} | bad
    g = greeks(args["F"], args["K"], args["T"], args["s"], True)

    for quantity in (g.price, g.delta, g.gamma, g.vega, g.theta, g.vanna, g.volga):
        assert math.isnan(f(quantity))


def test_scalars_return_floats_and_arrays_broadcast() -> None:
    assert isinstance(price(F, K, T, S, True), float)
    strikes_ = np.array([79_000.0, 80_000.0, 81_000.0])

    out = price(F, strikes_, T, S, True)

    assert isinstance(out, np.ndarray) and out.shape == (3,)
    assert f(price(F, 80_000.0, T, S, True)) == pytest.approx(float(out[1]))
    mixed = greeks(F, strikes_, T, S, np.array([True, False, True]))
    assert np.asarray(mixed.delta).shape == (3,)


def test_position_scaling_is_linear() -> None:
    g = greeks(F, K, T, S, False)
    scaled = g.scaled(0.25)

    assert f(scaled.price) == pytest.approx(0.25 * f(g.price))
    assert f(scaled.vega) == pytest.approx(0.25 * f(g.vega))


def test_time_to_expiry_is_act_365_and_never_negative() -> None:
    now = datetime(2026, 10, 18, 8, tzinfo=UTC)

    assert time_to_expiry_years(now, now + timedelta(days=365)) == pytest.approx(1.0)
    assert time_to_expiry_years(now, now + timedelta(hours=12)) == pytest.approx(0.5 / 365)
    assert time_to_expiry_years(now, now - timedelta(seconds=1)) == 0.0
    with pytest.raises(ValueError, match="timezone-aware"):
        time_to_expiry_years(datetime(2026, 10, 18), now)


def test_deribit_style_theta_scales_and_caps_inside_the_last_day() -> None:
    assert f(theta_deribit_style(-100.0, 500.0, 2 / 365)) == pytest.approx(-100.0)  # 2 days left
    assert f(theta_deribit_style(-100.0, 500.0, 0.5 / 365)) == pytest.approx(-50.0)  # half a day
    assert f(theta_deribit_style(-100.0, 20.0, 0.5 / 365)) == pytest.approx(
        -20.0
    )  # capped by value


@settings(max_examples=30)
@given(s=vols)
def test_atm_vega_matches_the_closed_form(s: float) -> None:
    # At the money (F == K) vega per unit sigma is F * sqrt(T) / sqrt(2 pi) at sigma = 0 and
    # d1 = sigma sqrt(T) / 2 otherwise; compare against the direct formula.
    d1 = 0.5 * s * math.sqrt(T)
    expected = F * math.sqrt(T) * math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / 100

    assert f(greeks(F, F, T, s, True).vega) == pytest.approx(expected, rel=1e-12)
