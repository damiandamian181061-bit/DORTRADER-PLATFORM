"""Black-76 against QuantLib-generated reference values (scripts/generate_black76_golden.py)."""

import json
import math
from pathlib import Path
from typing import Any

import pytest

from dortrader.pricing import IVStatus, greeks, implied_vol_scalar

pytestmark = pytest.mark.golden

GOLDEN = json.loads((Path(__file__).parent / "black76_reference.json").read_text(encoding="utf-8"))
CASES: list[dict[str, Any]] = GOLDEN["cases"]


def label(case: dict[str, Any]) -> str:
    kind = "C" if case["is_call"] else "P"
    moneyness = case["K"] / case["F"]
    return f"{kind} K/F={moneyness:g} T={case['T'] * 365:.4g}d s={case['sigma']} r={case['r']}"


def close(actual: float, expected: float, rel: float, abs_: float) -> bool:
    return math.isclose(actual, expected, rel_tol=rel, abs_tol=abs_)


@pytest.mark.parametrize("case", CASES, ids=[label(c) for c in CASES])
def test_price_and_greeks_match_quantlib(case: dict[str, Any]) -> None:
    g = greeks(case["F"], case["K"], case["T"], case["sigma"], case["is_call"], case["r"])

    assert close(float(g.price), case["price"], 1e-9, 1e-9)
    assert close(float(g.delta), case["delta"], 1e-9, 1e-12)
    assert close(float(g.gamma), case["gamma"], 1e-8, 1e-14)
    assert close(float(g.vega), case["vega_per_point"], 1e-9, 1e-9)
    # Theta, vanna and volga references are finite differences, so they are less exact.
    assert close(float(g.theta), case["theta_per_day"], 1e-5, 1e-6)
    assert close(float(g.vanna), case["vanna_per_point"], 1e-5, 1e-7)
    assert close(float(g.volga), case["volga_per_point2"], 1e-5, 1e-8)


@pytest.mark.parametrize("case", [c for c in CASES if c["iv_testable"]], ids=lambda c: label(c))
def test_implied_vol_matches_quantlib(case: dict[str, Any]) -> None:
    value, status = implied_vol_scalar(
        case["price"], case["F"], case["K"], case["T"], case["is_call"], case["r"]
    )
    vega_unit = case["vega_per_point"] * 100.0
    # Our price tolerance is 1e-12 * max(1, price); that bounds the achievable sigma accuracy.
    tolerance = 1e-9 + 20 * 1e-12 * max(1.0, case["price"]) / vega_unit

    assert status is IVStatus.OK
    assert abs(value - case["iv"]) <= tolerance


def test_reference_file_is_the_expected_size() -> None:
    assert len(CASES) == 560
    assert GOLDEN["quantlib_version"]
