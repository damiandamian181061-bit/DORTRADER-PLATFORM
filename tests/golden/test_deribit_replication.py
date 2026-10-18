"""Our Black-76 against what Deribit itself reports in live tickers (captured 2026-10-18).

The bounds below are not tuned to pass. Each term is a documented source of difference:
  * Deribit rounds mark_iv to 0.01 vol points, so sigma is uncertain by +-0.005 points;
  * Greeks and prices are rounded to 5 and 2 decimals;
  * mark_price and underlying_price in one ticker are not simultaneous. In these fixtures the
    forward mismatch needed to explain the price gap is at most 2.4 bps; we allow 5 bps.
See docs/validation/black76-vs-deribit.md for the observed numbers.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from dortrader.domain import ms_to_utc
from dortrader.pricing import (
    IVStatus,
    greeks,
    implied_vol_scalar,
    theta_deribit_style,
    time_to_expiry_years,
)
from tests.conftest import load_json

pytestmark = pytest.mark.golden

IV_ROUNDING_POINTS = 0.005
FORWARD_SIMULTANEITY = 5e-4  # 5 bps

EXPIRIES = {
    "ticker_atm_put": datetime(2026, 10, 20, 8, tzinfo=UTC),
    "ticker_far_wing": datetime(2026, 10, 20, 8, tzinfo=UTC),
    "ticker_sub_day_atm_call": datetime(2026, 10, 19, 8, tzinfo=UTC),
    "ticker_sub_day_otm_call": datetime(2026, 10, 19, 8, tzinfo=UTC),
}


def setup(name: str) -> tuple[dict[str, Any], float, float, bool, float]:
    ticker = load_json("deribit", f"{name}.json")["result"]
    instrument = ticker["instrument_name"]
    strike = float(instrument.split("-")[2])
    is_call = instrument.endswith("-C")
    years = time_to_expiry_years(ms_to_utc(ticker["timestamp"]), EXPIRIES[name])
    return ticker, strike, years, is_call, ticker["underlying_price"]


@pytest.mark.parametrize("name", EXPIRIES)
def test_greeks_match_deribit_within_explained_differences(name: str) -> None:
    ticker, K, T, is_call, F = setup(name)
    sigma = ticker["mark_iv"] / 100
    r = ticker["interest_rate"]
    g = greeks(F, K, T, sigma, is_call, r)
    theirs = ticker["greeks"]
    vol_pts = ticker["mark_iv"]
    forward_shift = F * FORWARD_SIMULTANEITY

    price_tol = 0.005 + IV_ROUNDING_POINTS * float(g.vega) + abs(float(g.delta)) * forward_shift
    assert abs(float(g.price) - ticker["mark_price"]) <= price_tol

    delta_tol = 1e-5 + IV_ROUNDING_POINTS * abs(float(g.vanna)) + float(g.gamma) * forward_shift
    assert abs(float(g.delta) - theirs["delta"]) <= delta_tol

    assert abs(float(g.gamma) - theirs["gamma"]) <= 5e-6

    vega_tol = 1e-5 + IV_ROUNDING_POINTS * abs(float(g.volga)) + abs(float(g.vanna)) * forward_shift
    assert abs(float(g.vega) - theirs["vega"]) <= vega_tol

    # Their theta is decay over min(1 day, remaining life), capped at the option's value.
    theta = float(theta_deribit_style(g.theta, ticker["mark_price"], T))
    theta_tol = abs(float(g.theta)) * (IV_ROUNDING_POINTS / vol_pts + FORWARD_SIMULTANEITY) + 1e-4
    assert abs(theta - theirs["theta"]) <= max(theta_tol, 2e-3)


@pytest.mark.parametrize("name", EXPIRIES)
def test_implied_vol_from_mark_price_matches_mark_iv(name: str) -> None:
    ticker, K, T, is_call, F = setup(name)
    g = greeks(F, K, T, ticker["mark_iv"] / 100, is_call, ticker["interest_rate"])

    iv, status = implied_vol_scalar(ticker["mark_price"], F, K, T, is_call, ticker["interest_rate"])

    gap_points = abs(iv * 100 - ticker["mark_iv"])
    allowed = IV_ROUNDING_POINTS + (0.005 + abs(float(g.delta)) * F * FORWARD_SIMULTANEITY) / float(
        g.vega
    )
    assert status is IVStatus.OK
    assert gap_points <= allowed


def test_the_one_day_theta_cap_only_matters_inside_a_day() -> None:
    ticker, K, T, is_call, F = setup("ticker_atm_put")  # 1.5 days left: no cap, no scaling
    g = greeks(F, K, T, ticker["mark_iv"] / 100, is_call)

    assert float(theta_deribit_style(g.theta, g.price, T)) == pytest.approx(float(g.theta))
