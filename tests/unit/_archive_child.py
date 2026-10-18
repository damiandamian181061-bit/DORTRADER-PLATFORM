"""Crash-test child: writes batches, prints an ACK after each flush, never exits on its own."""

import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa

from dortrader.clock import SystemClock
from dortrader.storage.parquet_archive import ParquetArchive

SCHEMA = pa.schema([("id", pa.int64())])


def main(directory: str) -> None:
    archive = ParquetArchive(
        Path(directory),
        SCHEMA,
        schema_version=1,
        clock=SystemClock(),
        run_id="child",
        flush_seconds=1e9,
    )
    next_id = 0
    while True:
        ids = list(range(next_id, next_id + 50))
        archive.append(pa.table({"id": ids}, schema=SCHEMA), ts=datetime.now(UTC))
        archive.flush()
        next_id += 50
        print(f"ACK {next_id}", flush=True)
        time.sleep(0.005)


if __name__ == "__main__":
    main(sys.argv[1])
