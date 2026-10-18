"""Score the volatility forecast baselines on backfilled history and write the report.

Run:  uv run python scripts/evaluate_vol_forecasts.py --data-dir data
          [--instrument BTC_USDC-PERPETUAL] [--cross-check BTC-PERPETUAL] [--test-days 365]

Needs the candle backfill first (apps.worker.backfill). Writes:
  data/evaluation/vol_forecasts.json         what the API serves
  docs/validation/vol-forecast-baselines.md  the human-readable report
The numbers are reported as they come out; no model is promoted by assumption.
"""

import argparse
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from dortrader.forecasting import EvaluationReport, default_models, evaluate
from dortrader.forecasting.models import HOURS_PER_YEAR, FloatArray, Garch
from dortrader.storage.bars import BarStore
from dortrader.storage.layout import bars_dir
from dortrader.volatility.realized import US, Candles, forward_realized_vol

HOUR = 3600
HORIZONS = (24, 168, 720)
HORIZON_NAMES = {24: "next 24 hours", 168: "next 7 days", 720: "next 30 days"}


def load(store: BarStore, instrument: str) -> Candles:
    bars = store.read(instrument, HOUR)
    if not bars:
        raise SystemExit(f"no hourly candles for {instrument}; run apps.worker.backfill first")
    return Candles.from_bars(bars)


def aligned_dvol(store: BarStore, grid: Candles) -> FloatArray | None:
    """DVOL (decimal) at each candle's close, on the candle grid; NaN where DVOL has none."""
    bars = store.read("DVOL-BTC", HOUR)
    if not bars:
        return None
    by_start = {int(b.start.timestamp() * US): b.close / 100.0 for b in bars}
    return np.array([by_start.get(int(s), np.nan) for s in grid.start], dtype=np.float64)


def run(
    store: BarStore, instrument: str, test_days: int
) -> tuple[EvaluationReport, Candles, FloatArray | None]:
    candles = load(store, instrument)
    dvol = aligned_dvol(store, candles)
    known_at = candles.known_at
    test_start = int(np.searchsorted(known_at, known_at[-1] - test_days * 86_400 * US))
    benchmarks = {"dvol_30d_implied": dvol} if dvol is not None else {}
    report = evaluate(
        candles.log_returns(),
        known_at,
        series=instrument,
        horizons=HORIZONS,
        test_start_index=test_start,
        model_factory=lambda: default_models(benchmarks),
    )
    return report, candles, dvol


def fmt(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def horizon_table(report: EvaluationReport) -> list[str]:
    lines = []
    for horizon in report.horizons:
        lines += [
            f"### Forecasting the {HORIZON_NAMES[horizon.horizon_hours]}",
            "",
            f"{horizon.origins} scored forecasts, one every {horizon.step_hours} hours.",
            "",
            "| Model | QLIKE | MAE (vol pts) | Bias (vol pts) | MZ slope | MZ R2 | "
            "QLIKE gain vs naive (95% CI) | Verdict |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for s in sorted(horizon.scores, key=lambda s: s.qlike):
            gain = (
                "baseline"
                if s.gain_vs_naive is None
                else f"{s.gain_vs_naive:+.3f} ({s.gain_low:+.3f}, {s.gain_high:+.3f})"
            )
            lines.append(
                f"| {s.model} | {s.qlike:.3f} | {s.mae_vol * 100:.1f} | {s.bias_vol * 100:+.1f} | "
                f"{fmt(s.mz_slope)} | {fmt(s.mz_r2)} | {gain} | {s.verdict} |"
            )
        lines.append("")
    return lines


def risk_premium_lines(candles: Candles, dvol: FloatArray | None, test_start: int) -> list[str]:
    if dvol is None:
        return []
    realized = forward_realized_vol(candles.log_returns(), 720, HOUR)
    origins = np.arange(test_start, len(realized), 24)
    keep = origins[np.isfinite(realized[origins]) & np.isfinite(dvol[origins])]
    if len(keep) < 10:
        return []
    implied, forward = dvol[keep], realized[keep]
    return [
        "## Implied versus realized, 30 days",
        "",
        f"Over {len(keep)} daily origins in the test window, DVOL averaged "
        f"{implied.mean() * 100:.1f} and the realized volatility of the following 30 days averaged "
        f"{forward.mean() * 100:.1f} (vol points). The mean gap in variance terms "
        f"(implied minus realized) was {np.mean(implied**2 - forward**2):+.4f}. Origins one day apart "
        "overlap, so this is a descriptive average, not an independent sample.",
        "",
    ]


def garch_diagnostics_lines(candles: Candles) -> list[str]:
    """Where GARCH's forecasts head: its fitted long-run volatility against what was realized."""
    returns = candles.log_returns()
    model = Garch()
    model.fit(returns, 24)
    p = model.parameters
    persistence = p["alpha[1]"] + p["beta[1]"]
    recent = returns[-model.train_hours :]
    recent = recent[np.isfinite(recent)]
    sample = float(np.std(recent) * math.sqrt(HOURS_PER_YEAR))
    if persistence >= 1:
        long_run = "undefined (persistence is at or above 1)"
    else:
        long_run = f"{math.sqrt(p['omega'] / 1e4 / (1 - persistence) * HOURS_PER_YEAR) * 100:.0f}"
    return [
        "## GARCH diagnostics",
        "",
        f"Fitted on the latest {len(recent):,} hourly returns: alpha {p['alpha[1]']:.3f}, beta "
        f"{p['beta[1]']:.3f}, persistence {persistence:.4f}. Its multi-step forecasts head toward the "
        f"fitted long-run volatility of {long_run} vol points, while the realized volatility over the "
        f"same returns was {sample * 100:.0f}. Hourly returns are heavy-tailed, which a Gaussian GARCH "
        "reads as a high long-run variance; a Student-t variant was tried on three origins and "
        "fitted persistence of 1.0, so it was not added.",
        "",
    ]


def write_report(
    primary: tuple[EvaluationReport, Candles, FloatArray | None],
    cross: tuple[EvaluationReport, Candles, FloatArray | None] | None,
    path: Path,
) -> None:
    report, candles, dvol = primary
    first = datetime.fromtimestamp(candles.start[0] / US, tz=UTC)
    test_start = int(np.searchsorted(candles.known_at, report.window_start.timestamp() * US))
    lines = [
        "# Volatility forecast baselines",
        "",
        f"Series `{report.series}`, hourly candles from {first:%Y-%m-%d} to "
        f"{report.window_end:%Y-%m-%d %H:%M} UTC ({len(candles.close):,} candles). Forecasts were "
        f"scored out of sample from {report.window_start:%Y-%m-%d} to the end of the data. Generated "
        f"{report.generated_at:%Y-%m-%d %H:%M} UTC from data `{report.data_hash}`.",
        "",
        "**How to read it.** Each model forecasts the annualized realized volatility over the next "
        "24 hours, 7 days or 30 days, seeing only data up to the forecast time. QLIKE is the loss "
        "(lower is better, zero is a perfect forecast). The gain column is the naive baseline's QLIKE "
        "minus the model's, with a bootstrap interval; a model is called better only if the whole "
        "interval is above zero. MZ slope and R2 come from regressing realized on forecast variance "
        "(slope near 1 means well calibrated). Forecasts are spaced one horizon apart so scored "
        "windows do not overlap, which leaves few points at the longer horizons and wide intervals.",
        "",
        "The naive model repeats the last window's realized vol. `dvol_30d_implied` is Deribit's "
        "30-day implied volatility index used as a forecast; it is a 30-day measure, so it is only "
        "like-for-like at the 30-day horizon.",
        "",
        *horizon_table(report),
        *risk_premium_lines(candles, dvol, test_start),
        *garch_diagnostics_lines(candles),
    ]
    if cross is not None:
        cross_report = cross[0]
        lines += [
            f"## Cross-check on `{cross_report.series}`",
            "",
            f"The same evaluation on the inverse perpetual (candles from "
            f"{datetime.fromtimestamp(cross[1].start[0] / US, tz=UTC):%Y-%m-%d}); conclusions should "
            "agree if they are not artefacts of one series.",
            "",
            *horizon_table(cross_report),
        ]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--instrument", default="BTC_USDC-PERPETUAL")
    parser.add_argument("--cross-check", default="BTC-PERPETUAL")
    parser.add_argument("--test-days", type=int, default=365)
    parser.add_argument(
        "--report", type=Path, default=Path("docs/validation/vol-forecast-baselines.md")
    )
    args = parser.parse_args()

    store = BarStore(bars_dir(args.data_dir))
    primary = run(store, args.instrument, args.test_days)
    cross = run(store, args.cross_check, args.test_days) if args.cross_check else None

    out = args.data_dir / "evaluation"
    out.mkdir(parents=True, exist_ok=True)
    (out / "vol_forecasts.json").write_text(primary[0].model_dump_json(indent=1), encoding="utf-8")
    write_report(primary, cross, args.report)
    print(args.report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
