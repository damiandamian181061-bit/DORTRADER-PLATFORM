"""Append-only, zstd-compressed Parquet archive with a checksum manifest and crash recovery.

Layout:  <directory>/date=YYYY-MM-DD/hour=HH/part-<run>-<seq>.parquet
Manifest: <directory>/_manifest.jsonl, one line per finished file (rows, sha256, schema version).

Rows are buffered in memory and written as a whole file to a temporary name, then renamed. A file
is therefore either complete or absent; a crash can lose only rows that were never flushed.
Single writer per directory. `append` returns nothing and `flush` is the acknowledgement point.
"""

import hashlib
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from dortrader.clock import Clock
from dortrader.log import get_logger

log = get_logger("parquet_archive")

MANIFEST = "_manifest.jsonl"
QUARANTINE = "_quarantine"
VERSION_KEY = b"dortrader.schema_version"


class ArchiveCorruptError(Exception):
    pass


class NoDataError(Exception):
    """No finished file overlaps the requested range."""


@dataclass
class RecoveryReport:
    promoted: list[str] = field(default_factory=list)
    quarantined: list[str] = field(default_factory=list)


@dataclass
class VerifyReport:
    files_checked: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _partition(directory: Path, ts: datetime) -> Path:
    return directory / f"date={ts:%Y-%m-%d}" / f"hour={ts:%H}"


class ParquetArchive:
    def __init__(
        self,
        directory: Path,
        schema: pa.Schema,
        *,
        schema_version: int,
        clock: Clock,
        run_id: str,
        flush_seconds: float = 300.0,
        max_rows: int = 200_000,
        sort_by: list[tuple[str, str]] | None = None,
    ) -> None:
        self.directory = directory
        self._schema = schema.with_metadata({VERSION_KEY: str(schema_version).encode()})
        self._version = schema_version
        self._clock = clock
        self._run_id = run_id
        self._flush_seconds = flush_seconds
        self._max_rows = max_rows
        self._sort_by = sort_by
        self._buffers: dict[Path, list[pa.Table]] = defaultdict(list)
        self._buffered_rows = 0
        self._seq = 0
        self._last_flush = clock.monotonic()
        directory.mkdir(parents=True, exist_ok=True)

    def append(self, table: pa.Table, *, ts: datetime) -> None:
        """Buffer rows under the partition for `ts` (UTC). Call flush() to make them durable."""
        if not table.schema.equals(self._schema, check_metadata=False):
            raise ValueError(f"table schema does not match archive schema:\n{table.schema}")
        if table.num_rows == 0:
            return
        self._buffers[_partition(self.directory, ts)].append(table)
        self._buffered_rows += table.num_rows
        if (
            self._buffered_rows >= self._max_rows
            or self._clock.monotonic() - self._last_flush >= self._flush_seconds
        ):
            try:
                self.flush()
            except OSError as error:
                # Disk trouble must not drop rows or desynchronise datasets: keep buffering and
                # retry on the next append. An explicit flush() still raises.
                log.error(
                    "flush failed, rows stay buffered", dir=str(self.directory), error=str(error)
                )

    def flush(self) -> list[Path]:
        written: list[Path] = []
        for partition, tables in list(self._buffers.items()):
            table = pa.concat_tables(tables).replace_schema_metadata(self._schema.metadata)
            if self._sort_by:
                table = table.sort_by(self._sort_by)
            partition.mkdir(parents=True, exist_ok=True)
            final = partition / f"part-{self._run_id}-{self._seq:05d}.parquet"
            self._seq += 1
            temporary = final.with_name(final.name + ".tmp")
            pq.write_table(table, temporary, compression="zstd")
            with temporary.open("r+b") as handle:  # Windows needs write access to fsync
                os.fsync(handle.fileno())
            os.replace(temporary, final)
            self._record(final, table.num_rows, recovered=False)
            written.append(final)
            del self._buffers[partition]
        self._buffered_rows = 0
        self._last_flush = self._clock.monotonic()
        return written

    def close(self) -> None:
        self.flush()

    def _record(self, path: Path, rows: int, *, recovered: bool) -> None:
        entry = {
            "file": path.relative_to(self.directory).as_posix(),
            "rows": rows,
            "sha256": _sha256(path),
            "schema_version": self._version,
            "written_at": self._clock.now().isoformat(),
            "recovered": recovered,
        }
        with (self.directory / MANIFEST).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def recover(self) -> RecoveryReport:
        """Settle files a crash left behind: promote readable temporaries, quarantine the rest."""
        report = RecoveryReport()
        for temporary in sorted(self.directory.rglob("*.parquet.tmp")):
            final = temporary.with_name(temporary.name.removesuffix(".tmp"))
            try:
                rows = pq.read_metadata(temporary).num_rows
                pq.read_table(temporary)
            except (OSError, pa.ArrowException):
                target = self.directory / QUARANTINE / temporary.name
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(temporary, target)
                report.quarantined.append(temporary.name)
                continue
            os.replace(temporary, final)
            self._record(final, rows, recovered=True)
            report.promoted.append(final.name)
        return report


_PARTITION = re.compile(r"date=(\d{4}-\d{2}-\d{2})/hour=(\d{2})/")


def _partition_start(relative: str) -> datetime | None:
    match = _PARTITION.search(relative)
    if match is None:
        return None
    return datetime.fromisoformat(f"{match[1]}T{match[2]}:00:00").replace(tzinfo=UTC)


def _overlaps(relative: str, start: datetime | None, end: datetime | None) -> bool:
    """Whether the hour partition holding `relative` can contain rows in [start, end]."""
    hour_start = _partition_start(relative)
    if hour_start is None or (start is None and end is None):
        return True
    if end is not None and hour_start > end:
        return False
    return not (start is not None and hour_start + timedelta(hours=1) <= start)


def verify_archive(
    directory: Path, *, start: datetime | None = None, end: datetime | None = None
) -> VerifyReport:
    """Check manifest, checksums and row counts; with start/end only for overlapping partitions."""
    report = VerifyReport()
    listed: set[str] = set()
    manifest = directory / MANIFEST
    entries = (
        [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line]
        if manifest.exists()
        else []
    )
    for entry in entries:
        name = entry["file"]
        listed.add(name)
        if not _overlaps(name, start, end):
            continue
        path = directory / name
        report.files_checked += 1
        if not path.exists():
            report.problems.append(f"{name}: listed in manifest but missing")
        elif _sha256(path) != entry["sha256"]:
            report.problems.append(f"{name}: checksum mismatch")
        else:
            try:
                if pq.read_metadata(path).num_rows != entry["rows"]:
                    report.problems.append(f"{name}: row count differs from manifest")
            except (OSError, pa.ArrowException):
                report.problems.append(f"{name}: unreadable")
    for path in directory.rglob("*.parquet"):
        name = path.relative_to(directory).as_posix()
        if name.startswith(QUARANTINE) or name in listed or not _overlaps(name, start, end):
            continue
        report.problems.append(f"{name}: on disk but not in manifest")
    return report


def read_archive(
    directory: Path,
    *,
    schema_version: int,
    start: datetime | None = None,
    end: datetime | None = None,
) -> pa.Table:
    """Read finished files, refusing unverified or wrong-version data.

    With start/end only the hour partitions overlapping that range are verified and read. They are
    returned whole, so callers still filter rows by their own timestamp column.
    """
    report = verify_archive(directory, start=start, end=end)
    if not report.ok:
        raise ArchiveCorruptError("; ".join(report.problems))
    tables = []
    for path in sorted(directory.rglob("*.parquet")):
        relative = path.relative_to(directory).as_posix()
        if relative.startswith(QUARANTINE) or not _overlaps(relative, start, end):
            continue
        table = pq.read_table(path)
        found = (table.schema.metadata or {}).get(VERSION_KEY)
        if found != str(schema_version).encode():
            raise ArchiveCorruptError(
                f"{path.name}: schema version {found!r}, expected {schema_version}"
            )
        tables.append(table)
    if not tables:
        raise NoDataError(f"no data files under {directory} for the requested range")
    return pa.concat_tables(tables)


def time_bounds(directory: Path, column: str = "received_at") -> tuple[datetime, datetime] | None:
    """Earliest and latest `column` value, from file statistics of the first and last partitions."""
    files = sorted(
        p
        for p in directory.rglob("*.parquet")
        if not p.relative_to(directory).as_posix().startswith(QUARANTINE)
    )
    if not files:
        return None

    def bounds(paths: list[Path]) -> list[datetime]:
        values: list[datetime] = []
        for path in paths:
            meta = pq.read_metadata(path)
            index = meta.schema.names.index(column)
            for group in range(meta.num_row_groups):
                stats = meta.row_group(group).column(index).statistics
                if stats is not None and stats.has_min_max:
                    values.extend([stats.min, stats.max])
        return values

    first_partition = files[0].parent
    last_partition = files[-1].parent
    low = bounds([f for f in files if f.parent == first_partition])
    high = bounds([f for f in files if f.parent == last_partition])
    if not low or not high:
        return None
    return min(low), max(high)
