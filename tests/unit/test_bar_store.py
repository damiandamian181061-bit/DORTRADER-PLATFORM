from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from dortrader.domain import Bar
from dortrader.storage.bars import BarStore, find_gaps

HOUR = 3600
T0 = datetime(2026, 1, 31, 22, 0, tzinfo=UTC)


def bar(start: datetime, close: float = 100.0, instrument: str = "X", step: int = HOUR) -> Bar:
    return Bar(
        instrument=instrument,
        step_seconds=step,
        start=start,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=1.0,
    )


def hourly(count: int, first: datetime = T0) -> list[Bar]:
    return [bar(first + timedelta(hours=i), 100.0 + i) for i in range(count)]


def test_bars_round_trip_across_a_month_boundary(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    bars = hourly(6)  # 22:00 and 23:00 on 31 Jan, then 00:00 to 03:00 on 1 Feb

    assert store.upsert(bars) == 6

    assert store.read("X", HOUR) == bars
    files = sorted(p.name for p in tmp_path.rglob("*.parquet"))
    assert files == ["2026-01.parquet", "2026-02.parquet"]


def test_upserting_the_same_bars_again_changes_nothing(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    store.upsert(hourly(4))
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*.parquet")}

    assert store.upsert(hourly(4)) == 0

    assert {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*.parquet")} == before


def test_a_revised_candle_replaces_the_stored_one(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    store.upsert(hourly(3))
    last = hourly(3)[-1].start

    assert store.upsert([bar(last, close=555.0)]) == 1

    stored = store.read("X", HOUR)
    assert len(stored) == 3 and stored[-1].close == 555.0 and stored[0].close == 100.0


def test_range_reads_and_bounds(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    bars = hourly(10)
    store.upsert(bars)

    window = store.read("X", HOUR, start=bars[3].start, end=bars[6].start)

    assert [b.start for b in window] == [b.start for b in bars[3:7]]
    assert store.bounds("X", HOUR) == (bars[0].start, bars[-1].start)
    assert store.read("X", HOUR, start=datetime(2030, 1, 1, tzinfo=UTC)) == []


def test_series_are_kept_apart(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    store.upsert(
        [bar(T0, instrument="A"), bar(T0, instrument="B"), bar(T0, instrument="A", step=60)]
    )

    assert len(store.read("A", HOUR)) == 1 and len(store.read("B", HOUR)) == 1
    assert len(store.read("A", 60)) == 1 and store.read("C", HOUR) == []
    assert store.bounds("C", HOUR) is None


def test_a_failed_write_leaves_the_previous_file_and_no_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = BarStore(tmp_path)
    store.upsert(hourly(3))
    real = pq.write_table

    def broken(*args: Any, **kwargs: Any) -> None:
        real(*args, **kwargs)  # the temporary file gets written...
        raise OSError("disk full")  # ...but the write is reported as failed

    monkeypatch.setattr(pq, "write_table", broken)
    with pytest.raises(OSError):
        store.upsert([bar(T0, close=999.0)])
    monkeypatch.undo()

    assert [b.close for b in store.read("X", HOUR)] == [100.0, 101.0, 102.0]
    assert list(tmp_path.rglob("*.tmp")) == []


def test_find_gaps_reports_holes_without_filling_them() -> None:
    starts = [T0, T0 + timedelta(hours=1), T0 + timedelta(hours=4), T0 + timedelta(hours=5)]

    gaps = find_gaps(starts, HOUR)

    assert len(gaps) == 1
    assert gaps[0].after == starts[1] and gaps[0].before == starts[2] and gaps[0].missing == 2
    assert find_gaps(starts[:2], HOUR) == [] and find_gaps([], HOUR) == []
