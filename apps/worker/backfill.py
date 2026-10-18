"""Backfill and refresh underlying candles and the DVOL index into the bar store.

Run:  uv run python -m apps.worker.backfill
One pass fetches everything missing since the last stored candle (re-fetching the last two so a
still-forming candle is replaced) and logs coverage. Set DORTRADER_BACKFILL__REFRESH_SECONDS to
keep refreshing. Re-running is always safe: the store upserts.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from dortrader.clock import Clock, SystemClock
from dortrader.config import BackfillSettings, Settings, get_settings
from dortrader.domain import Bar
from dortrader.log import configure_logging, get_logger
from dortrader.market_data.deribit.rest import DeribitRestClient, Envelope
from dortrader.market_data.normalize import parse_chart, parse_vol_index
from dortrader.storage.bars import BarStore, find_gaps
from dortrader.storage.layout import bars_dir

log = get_logger("backfill")
OVERLAP_CANDLES = 2  # always re-fetch this many stored candles, so a forming one is replaced
MAX_VOL_INDEX_PAGES = 500


class CandleClient(Protocol):
    async def get_tradingview_chart_data(
        self, instrument_name: str, step_seconds: int, start_ms: int, end_ms: int
    ) -> Envelope: ...

    async def get_volatility_index_data(
        self, currency: str, step_seconds: int, start_ms: int, end_ms: int
    ) -> Envelope: ...


@dataclass
class SeriesReport:
    instrument: str
    step_seconds: int
    fetched: int = 0
    changed: int = 0
    first: datetime | None = None
    last: datetime | None = None
    candles: int = 0
    gaps: int = 0
    missing: int = 0
    error: str | None = None
    unexpected_fields: set[str] = field(default_factory=set)


def plan_windows(
    start: datetime, end: datetime, step_seconds: int, max_candles: int
) -> list[tuple[datetime, datetime]]:
    """Consecutive, non-overlapping [start, end] windows, each holding at most max_candles
    candles (both ends inclusive), covering start to end."""
    span = timedelta(seconds=step_seconds * (max_candles - 1))
    step = timedelta(seconds=step_seconds)
    windows = []
    cursor = start
    while cursor <= end:
        last = min(cursor + span, end)
        windows.append((cursor, last))
        cursor = last + step
    return windows


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


class Backfill:
    def __init__(
        self, settings: BackfillSettings, client: CandleClient, store: BarStore, clock: Clock
    ) -> None:
        self._cfg = settings
        self._client = client
        self._store = store
        self._clock = clock

    def _resume_from(self, instrument: str, step_seconds: int) -> datetime:
        bounds = self._store.bounds(instrument, step_seconds)
        if bounds is None:
            return self._cfg.history_start
        resume = bounds[1] - timedelta(seconds=step_seconds * OVERLAP_CANDLES)
        return max(resume, self._cfg.history_start)

    async def run_once(self) -> list[SeriesReport]:
        reports = []
        for instrument in self._cfg.instruments:
            for step in self._cfg.step_seconds:
                reports.append(await self._guarded(instrument, step, self._chart_series))
        name = f"DVOL-{self._cfg.vol_index_currency}"
        for step in self._cfg.step_seconds:
            reports.append(await self._guarded(name, step, self._vol_index_series))
        return reports

    async def _guarded(self, instrument: str, step: int, fetch):  # type: ignore[no-untyped-def]
        report = SeriesReport(instrument, step)
        try:
            await fetch(instrument, step, report)
        except Exception as error:  # one failing series must not stop the others
            report.error = f"{type(error).__name__}: {error}"
            log.error("series failed", instrument=instrument, step=step, error=report.error)
        self._coverage(report)
        return report

    async def _chart_series(self, instrument: str, step: int, report: SeriesReport) -> None:
        start = self._resume_from(instrument, step)
        now = self._clock.now()
        for first, last in plan_windows(start, now, step, self._cfg.max_candles_per_request):
            envelope = await self._client.get_tradingview_chart_data(
                instrument, step, _ms(first), _ms(last)
            )
            parsed = parse_chart(envelope.result, instrument=instrument, step_seconds=step)
            self._store_bars(parsed.items, report)
            report.unexpected_fields |= parsed.unexpected_fields

    async def _vol_index_series(self, instrument: str, step: int, report: SeriesReport) -> None:
        start = self._resume_from(instrument, step)
        end = self._clock.now()
        for _ in range(MAX_VOL_INDEX_PAGES):
            envelope = await self._client.get_volatility_index_data(
                self._cfg.vol_index_currency, step, _ms(start), _ms(end)
            )
            parsed, continuation = parse_vol_index(
                envelope.result, name=instrument, step_seconds=step
            )
            self._store_bars(parsed.items, report)
            report.unexpected_fields |= parsed.unexpected_fields
            if continuation is None or continuation <= _ms(start):
                return
            end = datetime.fromtimestamp(continuation / 1000, tz=end.tzinfo)
        raise RuntimeError(f"volatility index paging did not finish in {MAX_VOL_INDEX_PAGES} pages")

    def _store_bars(self, bars: list[Bar], report: SeriesReport) -> None:
        report.fetched += len(bars)
        report.changed += self._store.upsert(bars)

    def _coverage(self, report: SeriesReport) -> None:
        bars = self._store.read(report.instrument, report.step_seconds)
        gaps = find_gaps([b.start for b in bars], report.step_seconds)
        report.candles = len(bars)
        report.first = bars[0].start if bars else None
        report.last = bars[-1].start if bars else None
        report.gaps = len(gaps)
        report.missing = sum(g.missing for g in gaps)
        if report.unexpected_fields:
            log.warning(
                "unexpected exchange fields",
                instrument=report.instrument,
                fields=sorted(report.unexpected_fields),
            )
        log.info(
            "series",
            instrument=report.instrument,
            step=report.step_seconds,
            fetched=report.fetched,
            changed=report.changed,
            candles=report.candles,
            first=report.first.isoformat() if report.first else None,
            last=report.last.isoformat() if report.last else None,
            gaps=report.gaps,
            missing=report.missing,
            error=report.error,
        )


async def _amain(settings: Settings) -> None:
    clock = SystemClock()
    client = DeribitRestClient(settings.deribit, clock)
    backfill = Backfill(
        settings.backfill, client, BarStore(bars_dir(settings.recorder.data_dir)), clock
    )
    try:
        while True:
            await backfill.run_once()
            if settings.backfill.refresh_seconds <= 0:
                return
            await asyncio.sleep(settings.backfill.refresh_seconds)
    finally:
        await client.aclose()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.json_logs)
    try:
        asyncio.run(_amain(settings))
    except KeyboardInterrupt:
        log.info("backfill stopped")


if __name__ == "__main__":
    main()
