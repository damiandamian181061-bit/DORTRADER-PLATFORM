# ruff: noqa: B023  (the closures are called inside the loop iteration that defines them)
"""Generate independent Black-76 reference values with QuantLib (never with our own code).

Run:  uv run --group reference python scripts/generate_black76_golden.py
Writes tests/golden/black76_reference.json. Commit the result; QuantLib is not needed in CI.

price/delta/gamma/vega come from QuantLib's BlackCalculator. Theta, vanna and volga are central
finite differences of QuantLib prices (Richardson-extrapolated), so they do not share any formula
with the code under test. IV is QuantLib's own solver run to 1e-14.
"""

import json
import math
from itertools import product
from pathlib import Path

import QuantLib as ql

OUT = Path(__file__).parent.parent / "tests" / "golden" / "black76_reference.json"

FORWARD = 81000.0
MONEYNESS = [0.5, 0.8, 0.95, 1.0, 1.05, 1.2, 2.0]
SECONDS = [3600.0, 86_400.0, 7 * 86_400.0, 0.25 * 365 * 86_400.0, 2 * 365 * 86_400.0]
SIGMAS = [0.05, 0.4, 1.0, 3.0]
RATES = [0.0, 0.03]
YEAR = 365.0 * 86_400.0


def ql_price(
    is_call: bool, strike: float, forward: float, t: float, sigma: float, r: float
) -> float:
    kind = ql.Option.Call if is_call else ql.Option.Put
    return float(ql.blackFormula(kind, strike, forward, sigma * math.sqrt(t), math.exp(-r * t)))


def richardson(f, x: float, h: float) -> float:  # type: ignore[no-untyped-def]
    d1 = (f(x + h) - f(x - h)) / (2 * h)
    d2 = (f(x + h / 2) - f(x - h / 2)) / h
    return (4 * d2 - d1) / 3


def main() -> None:
    cases = []
    for m, secs, sigma, r, is_call in product(MONEYNESS, SECONDS, SIGMAS, RATES, [True, False]):
        t = secs / YEAR
        strike = FORWARD * m
        kind = ql.Option.Call if is_call else ql.Option.Put
        df = math.exp(-r * t)
        std = sigma * math.sqrt(t)
        calc = ql.BlackCalculator(ql.PlainVanillaPayoff(kind, strike), FORWARD, std, df)
        price = float(calc.value())
        intrinsic = df * max((FORWARD - strike) if is_call else (strike - FORWARD), 0.0)
        vega_unit = float(calc.vega(t))

        def p_sigma(s: float) -> float:
            return ql_price(is_call, strike, FORWARD, t, s, r)

        def vega_sigma(s: float) -> float:
            return float(
                ql.BlackCalculator(
                    ql.PlainVanillaPayoff(kind, strike), FORWARD, s * math.sqrt(t), df
                ).vega(t)
            )

        def delta_sigma(s: float) -> float:
            return float(
                ql.BlackCalculator(
                    ql.PlainVanillaPayoff(kind, strike), FORWARD, s * math.sqrt(t), df
                ).deltaForward()
            )

        theta_year = -richardson(
            lambda tt: ql_price(is_call, strike, FORWARD, tt, sigma, r), t, t * 1e-3
        )
        iv_testable = (price - intrinsic) > 1e-6 * FORWARD and vega_unit > 1e-3
        iv = None
        if iv_testable:
            iv = float(
                ql.blackFormulaImpliedStdDev(
                    kind, strike, FORWARD, price, df, 0.0, 0.2 * math.sqrt(t), 1e-14, 200
                )
            ) / math.sqrt(t)
        h = sigma * 1e-3
        cases.append(
            {
                "F": FORWARD,
                "K": strike,
                "T": t,
                "sigma": sigma,
                "r": r,
                "is_call": is_call,
                "price": price,
                "delta": float(calc.deltaForward()),
                "gamma": float(calc.gammaForward()),
                "vega_per_point": vega_unit / 100.0,
                "theta_per_day": theta_year / 365.0,
                "vanna_per_point": richardson(delta_sigma, sigma, h) / 100.0,
                "volga_per_point2": richardson(vega_sigma, sigma, h) / 100.0 / 100.0,
                "iv_testable": iv_testable,
                "iv": iv,
            }
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"quantlib_version": ql.__version__, "cases": cases}, indent=0) + "\n",
        encoding="utf-8",
    )
    print(f"{len(cases)} cases, QuantLib {ql.__version__} -> {OUT}")


if __name__ == "__main__":
    main()
