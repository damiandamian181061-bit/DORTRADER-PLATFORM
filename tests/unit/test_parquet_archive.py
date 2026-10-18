import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dortrader.clock import ManualClock
from dortrader.domain import OptionQuote, QualityFlag
from dortrader.storage.parquet_archive import (
    VERSION_KEY,
    ArchiveCorruptError,
    ParquetArchive,
    read_archive,
    verify_archive,
)
from dortrader.storage.tables import (
    QUOTES_SCHEMA,
    QUOTES_VERSION,
    quotes_to_table,
    table_to_quotes,
)

T0 = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
ID_SCHEMA = pa.schema([("id", pa.int64())])
REPO_ROOT = Path(__file__).parent.parent.parent


def ids(*values: int) -> pa.Table:
    return pa.table({"id": list(values)}, schema=ID_SCHEMA)


def make_archive(
    tmp_path: Path,
    clock: ManualClock | None = None,
    flush_seconds: float = 300.0,
    max_rows: int = 200_000,
) -> ParquetArchive:
    return ParquetArchive(
        tmp_path,
        ID_SCHEMA,
        schema_version=1,
        clock=clock or ManualClock(T0),
        run_id="t",
        flush_seconds=flush_seconds,
        max_rows=max_rows,
    )


def quote(name: str, bid: float | None, flags: tuple[QualityFlag, ...] = ()) -> OptionQuote:
    return OptionQuote(
        instrument_name=name,
        source="deribit",
        channel="book_summary",
        received_at=T0,
        exchange_ts=T0,
        bid_price=bid,
        ask_price=840.0,
        mark_price=824.8,
        mark_iv=0.365,
        underlying_price=81117.5,
        underlying_index="BTC_USDC-23OCT26",
        interest_rate=0.0,
        open_interest=1.5,
        volume=None,
        last_price=None,
        flags=flags,
        raw_sha256="ab" * 32,
    )


def test_quotes_round_trip_through_parquet(tmp_path: Path) -> None:
    quotes = [
        quote("B", 800.0),
        quote("A", None, (QualityFlag.NO_BID, QualityFlag.IV_MISSING)),
    ]
    archive = ParquetArchive(
        tmp_path,
        QUOTES_SCHEMA,
        schema_version=QUOTES_VERSION,
        clock=ManualClock(T0),
        run_id="t",
        sort_by=[("instrument_name", "ascending")],
    )

    archive.append(quotes_to_table(quotes), ts=T0)
    archive.close()
    back = table_to_quotes(read_archive(tmp_path, schema_version=QUOTES_VERSION))

    assert [q.instrument_name for q in back] == ["A", "B"]  # sorted by instrument
    assert back[0] == quotes[1] and back[1] == quotes[0]


def test_rows_land_in_date_and_hour_partitions(tmp_path: Path) -> None:
    archive = make_archive(tmp_path)

    archive.append(ids(1), ts=datetime(2026, 10, 18, 12, 59, tzinfo=UTC))
    archive.append(ids(2), ts=datetime(2026, 10, 18, 13, 0, tzinfo=UTC))
    archive.append(ids(3), ts=datetime(2026, 10, 19, 0, 0, tzinfo=UTC))
    archive.close()

    partitions = sorted(
        p.parent.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.parquet")
    )
    assert partitions == [
        "date=2026-10-18/hour=12",
        "date=2026-10-18/hour=13",
        "date=2026-10-19/hour=00",
    ]


def test_nothing_is_durable_until_flush(tmp_path: Path) -> None:
    archive = make_archive(tmp_path, flush_seconds=1e9)

    archive.append(ids(1, 2), ts=T0)
    assert list(tmp_path.rglob("*.parquet")) == []

    archive.flush()
    assert len(list(tmp_path.rglob("*.parquet"))) == 1


def test_flushes_automatically_by_age_and_by_size(tmp_path: Path) -> None:
    clock = ManualClock(T0)
    archive = make_archive(tmp_path, clock, flush_seconds=60, max_rows=3)

    archive.append(ids(1), ts=T0)
    assert list(tmp_path.rglob("*.parquet")) == []
    clock.advance(61)
    archive.append(ids(2), ts=T0)  # age trigger
    assert len(list(tmp_path.rglob("*.parquet"))) == 1
    archive.append(ids(3, 4, 5), ts=T0)  # size trigger
    assert len(list(tmp_path.rglob("*.parquet"))) == 2


def test_wrong_schema_is_rejected(tmp_path: Path) -> None:
    archive = make_archive(tmp_path)

    with pytest.raises(ValueError, match="schema"):
        archive.append(pa.table({"other": [1]}), ts=T0)


def test_verify_detects_a_flipped_byte_and_reader_refuses(tmp_path: Path) -> None:
    archive = make_archive(tmp_path)
    archive.append(ids(1, 2, 3), ts=T0)
    archive.close()
    assert verify_archive(tmp_path).ok

    victim = next(tmp_path.rglob("*.parquet"))
    data = bytearray(victim.read_bytes())
    data[len(data) // 2] ^= 0xFF
    victim.write_bytes(bytes(data))

    assert "checksum mismatch" in verify_archive(tmp_path).problems[0]
    with pytest.raises(ArchiveCorruptError):
        read_archive(tmp_path, schema_version=1)


def test_reader_refuses_wrong_schema_version(tmp_path: Path) -> None:
    archive = make_archive(tmp_path)
    archive.append(ids(1), ts=T0)
    archive.close()

    with pytest.raises(ArchiveCorruptError, match="schema version"):
        read_archive(tmp_path, schema_version=2)


def test_verify_flags_files_missing_from_manifest(tmp_path: Path) -> None:
    archive = make_archive(tmp_path)
    archive.append(ids(1), ts=T0)
    archive.close()
    stray = tmp_path / "date=2026-10-18" / "hour=12" / "stray.parquet"
    pq.write_table(ids(9), stray)

    assert any("not in manifest" in p for p in verify_archive(tmp_path).problems)


def test_recovery_promotes_readable_temporaries_and_quarantines_garbage(tmp_path: Path) -> None:
    archive = make_archive(tmp_path)
    partition = tmp_path / "date=2026-10-18" / "hour=12"
    partition.mkdir(parents=True)
    with_version = ids(1, 2).replace_schema_metadata({VERSION_KEY: b"1"})
    pq.write_table(with_version, partition / "part-dead-00000.parquet.tmp")
    (partition / "part-dead-00001.parquet.tmp").write_bytes(b"not parquet")

    report = archive.recover()

    assert report.promoted == ["part-dead-00000.parquet"]
    assert report.quarantined == ["part-dead-00001.parquet.tmp"]
    assert verify_archive(tmp_path).ok
    assert read_archive(tmp_path, schema_version=1).num_rows == 2


def test_killing_the_writer_loses_no_acknowledged_rows(tmp_path: Path) -> None:
    child = subprocess.Popen(
        [sys.executable, "-m", "tests.unit._archive_child", str(tmp_path)],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None
    acked = 0
    try:
        for line in child.stdout:
            acked = int(line.split()[1])
            if acked >= 400:
                break
    finally:
        child.kill()
        child.wait()

    archive = make_archive(tmp_path)
    archive.recover()

    assert verify_archive(tmp_path).ok
    found = read_archive(tmp_path, schema_version=1)["id"].to_pylist()
    assert len(found) == len(set(found))
    assert set(range(acked)) <= set(found)


def test_a_failed_auto_flush_keeps_rows_buffered_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = make_archive(tmp_path, max_rows=1)
    real_write = pq.write_table
    failures = {"left": 1}

    def flaky(*args: Any, **kwargs: Any) -> None:
        if failures["left"]:
            failures["left"] -= 1
            raise OSError("disk full")
        real_write(*args, **kwargs)

    monkeypatch.setattr(pq, "write_table", flaky)

    archive.append(ids(1), ts=T0)  # auto-flush fails: no exception, row stays buffered
    assert list(tmp_path.rglob("*.parquet")) == []
    archive.append(ids(2), ts=T0)  # retried together with the new row
    archive.close()

    assert sorted(read_archive(tmp_path, schema_version=1)["id"].to_pylist()) == [1, 2]
