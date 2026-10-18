"""Candle storage: one Parquet file per instrument, candle size and month.

Candles are re-fetchable public data and the last one is still forming when fetched, so unlike
the append-only quote archive this store *upserts*: rows are keyed by candle start, a re-fetched
candle replaces the stored one, and re-running a backfill is always safe. Each file is replaced
atomically (write to a temporary name, fsync, rename), so a crash leaves the previous file. There
is no manifest: a damaged file is rebuilt by fetching again.

Layout:  <root>/instrument=<name>/step=<seconds>/<YYYY-MM>.parquet
"""

import os
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from dortrader.domain import Bar
from dortrader.storage.parquet_archive import time_bounds

BARS_SCHEMA = pa.schema(
    [
        ("instrument", pa.string()),
        ("step_seconds", pa.int32()),
        ("start", pa.timestamp("us", tz="UTC")),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
    ]
)


@dataclass(frozen=True)
class Gap:
    after: datetime  # last candle before the hole
    before: datetime  # first candle after it
    missing: int  # candles that should exist in between


def find_gaps(starts: Sequence[datetime], step_seconds: int) -> list[Gap]:
    """Holes in a sorted series of candle starts. Crypto trades 24/7, so every hole is a fact
    about the data (exchange downtime or a source gap) and is reported, not filled."""
    gaps = []
    for earlier, later in zip(starts, starts[1:], strict=False):
        missing = round((later - earlier).total_seconds() / step_seconds) - 1
        if missing > 0:
            gaps.append(Gap(earlier, later, missing))
    return gaps


@dataclass(frozen=True)
class Coverage:
    first: datetime
    last: datetime
    candles: int
    gaps: int  # holes in the series
    missing: int  # candles that should exist inside those holes


def _month_bounds(path: Path) -> tuple[datetime, datetime]:
    first = datetime.fromisoformat(f"{path.stem}-01T00:00:00").replace(tzinfo=UTC)
    following = datetime(first.year + (first.month == 12), first.month % 12 + 1, 1, tzinfo=UTC)
    return first, following


class BarStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _directory(self, instrument: str, step_seconds: int) -> Path:
        return self._root / f"instrument={instrument}" / f"step={step_seconds}"

    def upsert(self, bars: Sequence[Bar]) -> int:
        """Store bars, replacing any with the same instrument, step and start. Returns the
        number of rows that were new or changed."""
        groups: dict[tuple[str, int, str], list[Bar]] = defaultdict(list)
        for bar in bars:
            groups[(bar.instrument, bar.step_seconds, f"{bar.start:%Y-%m}")].append(bar)
        changed = 0
        for (instrument, step, month), incoming in groups.items():
            path = self._directory(instrument, step) / f"{month}.parquet"
            before: dict[datetime, dict[str, Any]] = {
                row["start"]: row for row in self._read_file(path)
            }
            merged = dict(before)
            for bar in incoming:
                merged[bar.start] = bar.model_dump()
            changed += sum(1 for k, v in merged.items() if before.get(k) != v)
            if merged != before:
                rows = [merged[k] for k in sorted(merged)]
                self._write_file(path, pa.Table.from_pylist(rows, schema=BARS_SCHEMA))
        return changed

    def read(
        self,
        instrument: str,
        step_seconds: int,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Bar]:
        """Candles with start in [start, end], sorted. Empty when nothing is stored."""
        directory = self._directory(instrument, step_seconds)
        if not directory.exists():
            return []
        tables = []
        for path in sorted(directory.glob("*.parquet")):
            first, following = _month_bounds(path)
            if (end is not None and first > end) or (start is not None and following <= start):
                continue
            tables.append(pq.read_table(path))
        if not tables:
            return []
        table = pa.concat_tables(tables)
        mask = pc.is_valid(table["start"])
        if start is not None:
            mask = pc.and_(mask, pc.greater_equal(table["start"], start))
        if end is not None:
            mask = pc.and_(mask, pc.less_equal(table["start"], end))
        return [Bar(**row) for row in table.filter(mask).sort_by("start").to_pylist()]

    def series(self) -> list[tuple[str, int]]:
        """Every (instrument, candle size) with stored candles."""
        found = []
        for instrument_dir in sorted(self._root.glob("instrument=*")):
            for step_dir in sorted(instrument_dir.glob("step=*")):
                if any(step_dir.glob("*.parquet")):
                    found.append(
                        (instrument_dir.name.split("=", 1)[1], int(step_dir.name.split("=", 1)[1]))
                    )
        return sorted(found)  # candle sizes in numeric order, not directory-name order

    def coverage(self, instrument: str, step_seconds: int) -> Coverage | None:
        """First and last candle, how many, and the holes, reading only the start column."""
        directory = self._directory(instrument, step_seconds)
        files = sorted(directory.glob("*.parquet")) if directory.exists() else []
        if not files:
            return None
        starts = np.sort(
            np.concatenate(
                [
                    pq.read_table(path, columns=["start"])["start"].to_numpy().astype("int64")
                    for path in files
                ]
            )
        )
        step_us = step_seconds * 1_000_000
        missing_per_gap = np.diff(starts) // step_us - 1
        holes = missing_per_gap[missing_per_gap > 0]
        return Coverage(
            first=datetime.fromtimestamp(starts[0] / 1e6, tz=UTC),
            last=datetime.fromtimestamp(starts[-1] / 1e6, tz=UTC),
            candles=len(starts),
            gaps=len(holes),
            missing=int(holes.sum()),
        )

    def bounds(self, instrument: str, step_seconds: int) -> tuple[datetime, datetime] | None:
        directory = self._directory(instrument, step_seconds)
        return time_bounds(directory, "start") if directory.exists() else None

    @staticmethod
    def _read_file(path: Path) -> list[dict[str, Any]]:
        return pq.read_table(path).to_pylist() if path.exists() else []

    @staticmethod
    def _write_file(path: Path, table: pa.Table) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        try:
            pq.write_table(table, temporary, compression="zstd")
            with temporary.open("r+b") as handle:  # Windows needs write access to fsync
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
