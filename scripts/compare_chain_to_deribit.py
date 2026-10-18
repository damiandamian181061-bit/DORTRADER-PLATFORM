"""Compare our implied vols with Deribit's mark_iv over a recorded time range.

Run:  uv run python scripts/compare_chain_to_deribit.py --data-dir data [--start ISO --end ISO]
          [--step-seconds 300] [--out docs/validation/chain-vs-deribit.md]

Only out-of-the-money contracts are compared (calls above the forward, puts below). In the money
the price is mostly intrinsic value and the inversion is ill-conditioned. Differences are in vol
points: ours (from Deribit's mark price) minus Deribit's mark_iv. Nothing here is tuned to pass;
the table says what the gaps are.
"""

import argparse
import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dortrader.pricing import ContractValuation, price_chain
from dortrader.replay import ChainStore, NoRecordedData

MONEYNESS = [(-math.inf, -0.10), (-0.10, -0.03), (-0.03, 0.03), (0.03, 0.10), (0.10, math.inf)]
DAYS = [(0.0, 1.0), (1.0, 3.0), (3.0, 10.0), (10.0, 45.0), (45.0, math.inf)]


def is_otm(v: ContractValuation) -> bool:
    return v.forward is not None and (
        (v.option_type == "call" and v.strike > v.forward)
        or (v.option_type == "put" and v.strike < v.forward)
    )


def bucket(value: float, edges: list[tuple[float, float]]) -> tuple[float, float]:
    return next(e for e in edges if e[0] <= value < e[1])


def label(edge: tuple[float, float], unit: str) -> str:
    low = "-inf" if edge[0] == -math.inf else f"{edge[0]:g}"
    high = "inf" if edge[1] == math.inf else f"{edge[1]:g}"
    return f"{low} to {high} {unit}"


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def percentile(sorted_values: list[float], q: float) -> float:
    return sorted_values[min(len(sorted_values) - 1, int(q * len(sorted_values)))]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--start", type=datetime.fromisoformat)
    parser.add_argument("--end", type=datetime.fromisoformat)
    parser.add_argument("--step-seconds", type=float, default=300.0)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    store = ChainStore(args.data_dir)
    recorded = store.recorded_range()
    if recorded is None:
        raise SystemExit(f"nothing recorded under {args.data_dir}")
    start = as_utc(args.start) if args.start else recorded[0]
    end = as_utc(args.end) if args.end else recorded[1]

    groups: dict[tuple[tuple[float, float], tuple[float, float]], list[float]] = defaultdict(list)
    snapshots = 0
    at = start
    while at <= end:
        try:
            pricing = price_chain(store.as_of(at))
            snapshots += 1
            for v in pricing.valuations:
                if is_otm(v) and v.iv_diff_points is not None and v.log_moneyness is not None:
                    key = (
                        bucket(v.log_moneyness, MONEYNESS),
                        bucket(v.years_to_expiry * 365.0, DAYS),
                    )
                    groups[key].append(v.iv_diff_points)
        except NoRecordedData:
            pass
        at += timedelta(seconds=args.step_seconds)

    lines = [
        "# Our implied vol versus Deribit mark_iv, out-of-the-money contracts",
        "",
        f"Range {start.isoformat()} to {end.isoformat()}, {snapshots} snapshots, "
        f"every {args.step_seconds:g} s. Differences in vol points (ours minus Deribit's).",
        "",
        "| Log-moneyness | Days to expiry | n | Mean | Median abs | P90 abs | P99 abs | Max abs |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for key in sorted(groups):
        diffs = groups[key]
        absolute = sorted(abs(d) for d in diffs)
        lines.append(
            f"| {label(key[0], '')} | {label(key[1], 'd')} | {len(diffs)} | "
            f"{statistics.fmean(diffs):+.3f} | {statistics.median(absolute):.3f} | "
            f"{percentile(absolute, 0.90):.3f} | {percentile(absolute, 0.99):.3f} | "
            f"{absolute[-1]:.3f} |"
        )
    report = "\n".join(lines) + "\n"
    print(report)
    if args.out:
        args.out.write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
