"""Tier 1 recorder: polls the Deribit option book summary and archives it to Parquet.

Run:  uv run python -m apps.worker.recorder

Parquet writes never depend on a database, so recording continues if one is down. Missed
cycles are written to the events dataset as gaps rather than hidden.
"""

import asyncio
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import pyarrow as pa

from dortrader.clock import Clock, SystemClock
from dortrader.config import RecorderSettings, Settings, get_settings
from dortrader.domain import Instrument, QualityFlag
from dortrader.log import configure_logging, get_logger
from dortrader.market_data.catalog import diff_catalog
from dortrader.market_data.deribit.rest import DeribitRestClient, Envelope
from dortrader.market_data.normalize import normalize_summary, parse_instruments
from dortrader.market_data.quality import QualityConfig
from dortrader.storage.parquet_archive import ParquetArchive
from dortrader.storage.tables import (
    CATALOG_SCHEMA,
    CATALOG_VERSION,
    EVENTS_SCHEMA,
    EVENTS_VERSION,
    QUOTES_SCHEMA,
    QUOTES_VERSION,
    RAW_SCHEMA,
    RAW_VERSION,
    event_row,
    instruments_to_table,
    quotes_to_table,
)

log = get_logger("recorder")
SLEEP_SLICE_SECONDS = 1.0


class MarketDataClient(Protocol):
    async def get_instruments(self, currency: str, kind: str) -> Envelope: ...

    async def get_book_summary_by_currency(self, currency: str, kind: str) -> Envelope: ...

    async def get_index_price(self, index_name: str) -> Envelope: ...


@dataclass(frozen=True)
class CycleOutcome:
    quotes: int
    flagged: int
    latency_seconds: float
    clock_offset_seconds: float | None


class Recorder:
    def __init__(
        self,
        settings: RecorderSettings,
        client: MarketDataClient,
        clock: Clock,
        *,
        run_id: str | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._cfg = settings
        self._client = client
        self._clock = clock
        self._sleep = sleep or asyncio.sleep
        self.run_id = run_id or f"{clock.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
        self._quality = QualityConfig(settings.wide_spread_ratio, settings.stale_exchange_seconds)
        base = settings.data_dir
        source = "source=deribit"
        self.raw = self._archive(
            base / "raw" / source / "channel=book_summary", RAW_SCHEMA, RAW_VERSION
        )
        self.quotes = self._archive(
            base / "normalized" / source / "channel=book_summary",
            QUOTES_SCHEMA,
            QUOTES_VERSION,
            sort_by=[("instrument_name", "ascending"), ("received_at", "ascending")],
        )
        self.catalog_archive = self._archive(
            base / "catalog" / source, CATALOG_SCHEMA, CATALOG_VERSION, flush_seconds=0.0
        )
        self.events = self._archive(
            base / "events", EVENTS_SCHEMA, EVENTS_VERSION, flush_seconds=0.0
        )
        self.catalog: dict[str, Instrument] = {}
        self._catalog_attempt: float | None = None
        self._catalog_day: str | None = None
        self._last_good: datetime | None = None
        self._reported_fields: set[frozenset[str]] = set()

    def _archive(
        self,
        directory: Path,
        schema: pa.Schema,
        version: int,
        *,
        flush_seconds: float | None = None,
        sort_by: list[tuple[str, str]] | None = None,
    ) -> ParquetArchive:
        return ParquetArchive(
            directory,
            schema,
            schema_version=version,
            clock=self._clock,
            run_id=self.run_id,
            flush_seconds=self._cfg.flush_seconds if flush_seconds is None else flush_seconds,
            sort_by=sort_by,
        )

    def start(self) -> None:
        for archive in (self.raw, self.quotes, self.catalog_archive, self.events):
            report = archive.recover()
            if report.promoted or report.quarantined:
                log.warning("recovered archive", dir=str(archive.directory), **report.__dict__)

    def close(self) -> None:
        for archive in (self.raw, self.quotes, self.catalog_archive, self.events):
            archive.close()

    def _event(self, kind: str, detail: dict[str, object]) -> None:
        now = self._clock.now()
        self.events.append(event_row(now, self.run_id, kind, detail), ts=now)
        self.events.flush()

    async def _refresh_catalog_if_due(self) -> None:
        retry = self._cfg.catalog_refresh_seconds if self.catalog else 60.0
        now = self._clock.monotonic()
        if self._catalog_attempt is not None and now - self._catalog_attempt < retry:
            return
        self._catalog_attempt = now
        try:
            envelope = await self._client.get_instruments(self._cfg.settlement_currency, "option")
            parsed = parse_instruments(
                envelope.result,
                underlying=self._cfg.underlying,
                settlement_currency=self._cfg.settlement_currency,
            )
        except Exception as error:  # keep recording with the previous catalog
            log.warning("catalog refresh failed", error=str(error))
            self._event("error", {"where": "catalog", "error": str(error)})
            return
        self._note_unexpected("instrument", parsed.unexpected_fields)
        first = not self.catalog
        diff = diff_catalog(self.catalog, parsed.items)
        today = f"{envelope.received_at:%Y-%m-%d}"
        if first or not diff.is_empty or today != self._catalog_day:
            table = instruments_to_table(envelope.received_at, parsed.items)
            self.catalog_archive.append(table, ts=envelope.received_at)
            self.catalog_archive.flush()
            self._catalog_day = today
        if not first and not diff.is_empty:
            self._event(
                "catalog_change",
                {
                    "added": [i.name for i in diff.added],
                    "removed": list(diff.removed),
                    "state_changes": [vars(c) for c in diff.state_changes],
                    "changed": list(diff.changed),
                },
            )
        self.catalog = {i.name: i for i in parsed.items}

    async def _clock_offset(self) -> float | None:
        # Not from the book summary: Deribit caches it, so its usOut lags by 0.5-2 s.
        name = f"{self._cfg.underlying}_{self._cfg.settlement_currency}".lower()
        try:
            return (await self._client.get_index_price(name)).clock_offset_seconds
        except Exception as error:
            log.warning("clock offset unavailable", error=str(error))
            return None

    def _note_unexpected(self, what: str, fields: frozenset[str]) -> None:
        if fields and fields not in self._reported_fields:
            self._reported_fields.add(fields)
            log.warning("unexpected exchange fields", what=what, fields=sorted(fields))
            self._event("schema_drift", {"what": what, "unexpected_fields": sorted(fields)})

    async def run_cycle(self) -> CycleOutcome:
        await self._refresh_catalog_if_due()
        envelope = await self._client.get_book_summary_by_currency(
            self._cfg.settlement_currency, "option"
        )
        rows = [r for r in envelope.result if r.get("base_currency") == self._cfg.underlying]
        parsed = normalize_summary(
            rows,
            underlying=self._cfg.underlying,
            received_at=envelope.received_at,
            raw_sha256=envelope.body_sha256,
            catalog=self.catalog,
            config=self._quality,
        )
        self._note_unexpected("book_summary", parsed.unexpected_fields)
        quotes = parsed.items
        offset = await self._clock_offset()
        if offset is not None and abs(offset) > self._cfg.clock_drift_seconds:
            quotes = [
                q.model_copy(
                    update={
                        "flags": tuple(
                            sorted({*q.flags, QualityFlag.CLOCK_DRIFT}, key=list(QualityFlag).index)
                        )
                    }
                )
                for q in quotes
            ]
            log.warning("clock drift", offset_seconds=offset)

        payload = json.dumps(rows, separators=(",", ":"), sort_keys=True)
        raw = pa.Table.from_pylist(
            [
                {
                    "received_at": envelope.received_at,
                    "exchange_ts": envelope.server_time,
                    "run_id": self.run_id,
                    "connection_epoch": 0,
                    "request_sha256": envelope.body_sha256,
                    "fragment_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                    "instrument_count": len(rows),
                    "payload": payload,
                }
            ],
            schema=RAW_SCHEMA,
        )
        self.raw.append(raw, ts=envelope.received_at)
        self.quotes.append(quotes_to_table(quotes), ts=envelope.received_at)

        gap_limit = self._cfg.poll_interval_seconds * self._cfg.gap_factor
        if self._last_good is not None:
            missed = (envelope.received_at - self._last_good).total_seconds()
            if missed > gap_limit:
                self._event(
                    "gap",
                    {
                        "start": self._last_good.isoformat(),
                        "end": envelope.received_at.isoformat(),
                        "seconds": missed,
                    },
                )
                log.warning("recording gap", seconds=missed)
        self._last_good = envelope.received_at

        flagged = sum(1 for q in quotes if q.flags)
        log.info(
            "cycle",
            quotes=len(quotes),
            flagged=flagged,
            latency_seconds=round(envelope.latency_seconds, 3),
            clock_offset_seconds=None if offset is None else round(offset, 3),
        )
        return CycleOutcome(len(quotes), flagged, envelope.latency_seconds, offset)

    async def run(self, stop: asyncio.Event, *, max_cycles: int | None = None) -> None:
        self.start()
        cycles = 0
        try:
            while not stop.is_set():
                started = self._clock.monotonic()
                try:
                    await self.run_cycle()
                except Exception as error:  # a recorder must outlive any single bad cycle
                    log.error("cycle failed", error=str(error), error_type=type(error).__name__)
                    self._event("error", {"where": "cycle", "error": str(error)})
                cycles += 1
                if max_cycles is not None and cycles >= max_cycles:
                    break
                remaining = self._cfg.poll_interval_seconds - (self._clock.monotonic() - started)
                while remaining > 0 and not stop.is_set():
                    step = min(SLEEP_SLICE_SECONDS, remaining)
                    await self._sleep(step)
                    remaining -= step
        finally:
            self.close()


async def _amain(settings: Settings) -> None:
    clock = SystemClock()
    client = DeribitRestClient(settings.deribit, clock)
    recorder = Recorder(settings.recorder, client, clock)
    log.info("recorder starting", run_id=recorder.run_id, data_dir=str(settings.recorder.data_dir))
    try:
        await recorder.run(asyncio.Event())
    finally:
        await client.aclose()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.json_logs)
    try:
        asyncio.run(_amain(settings))
    except KeyboardInterrupt:
        log.info("recorder stopped")


if __name__ == "__main__":
    main()
