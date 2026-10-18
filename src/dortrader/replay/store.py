"""Point-in-time reads of the recorded chain.

Availability is decided by **receipt** time: a quote exists at `t` only if we had received it by
`t`. The instrument universe comes from the latest catalog snapshot at or before `t`.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pyarrow.compute as pc

from dortrader.domain import ChainSnapshot, Instrument, OptionQuote
from dortrader.replay.reducer import fold, snapshot
from dortrader.storage.layout import catalog_dir, quotes_dir
from dortrader.storage.parquet_archive import NoDataError, read_archive, time_bounds
from dortrader.storage.tables import (
    CATALOG_VERSION,
    QUOTES_VERSION,
    table_to_instruments,
    table_to_quotes,
)

CATALOG_LOOKBACK = timedelta(days=2)  # the recorder snapshots the catalog at least once a day


class NoRecordedData(Exception):
    """Nothing was recorded in the window that answers the request."""


class ChainStore:
    def __init__(
        self,
        data_dir: Path,
        *,
        stale_after_seconds: float = 90.0,
        quote_lookback: timedelta = timedelta(minutes=10),
    ) -> None:
        self._data_dir = data_dir
        self._stale_after = stale_after_seconds
        self._lookback = quote_lookback

    def recorded_range(self) -> tuple[datetime, datetime] | None:
        """First and last quote receipt time, or None if nothing is recorded."""
        return time_bounds(quotes_dir(self._data_dir), "received_at")

    def as_of(self, at: datetime) -> ChainSnapshot:
        if at.tzinfo is None:
            raise ValueError("at must be timezone-aware")
        quotes = self._quotes(at)
        instruments = self._instruments(at)
        return snapshot(fold({}, quotes), instruments, at, stale_after_seconds=self._stale_after)

    def _quotes(self, at: datetime) -> list[OptionQuote]:
        start = at - self._lookback
        try:
            table = read_archive(
                quotes_dir(self._data_dir), schema_version=QUOTES_VERSION, start=start, end=at
            )
        except NoDataError as error:
            raise NoRecordedData(f"no quotes recorded between {start} and {at}") from error
        in_window = pc.and_(
            pc.less_equal(table["received_at"], at), pc.greater_equal(table["received_at"], start)
        )
        table = table.filter(in_window).sort_by("received_at")
        if table.num_rows == 0:
            raise NoRecordedData(f"no quotes recorded between {start} and {at}")
        return table_to_quotes(table)

    def _instruments(self, at: datetime) -> list[Instrument]:
        try:
            table = read_archive(
                catalog_dir(self._data_dir),
                schema_version=CATALOG_VERSION,
                start=at - CATALOG_LOOKBACK,
                end=at,
            )
        except NoDataError as error:
            raise NoRecordedData(f"no instrument catalog recorded before {at}") from error
        before = table.filter(pc.less_equal(table["snapshot_at"], at))
        if before.num_rows == 0:
            raise NoRecordedData(f"no instrument catalog recorded before {at}")
        latest = pc.max(before["snapshot_at"])
        return table_to_instruments(before.filter(pc.equal(before["snapshot_at"], latest)))
