import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from apps.worker.backfill import Backfill, plan_windows
from dortrader.clock import ManualClock
from dortrader.config import BackfillSettings
from dortrader.market_data.deribit.rest import Envelope
from dortrader.storage.bars import BarStore

pytestmark = pytest.mark.anyio

HOUR = 3600
T0 = datetime(2026, 10, 1, tzinfo=UTC)


def ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


class FakeExchange:
    """Serves one candle per step with close = 100 + hours since T0, like a calm market."""

    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock
        self.available_from = T0
        self.missing: set[datetime] = set()
        self.close_override: dict[datetime, float] = {}
        self.broken: set[str] = set()
        self.extra_field = False
        self.vol_page_size = 1000
        self.chart_calls: list[tuple[str, int, int, int]] = []
        self.vol_calls: list[tuple[int, int]] = []

    def _starts(self, step: int, start_ms: int, end_ms: int) -> list[datetime]:
        first = max(start_ms, ms(self.available_from))
        cursor = -(-first // (step * 1000)) * step * 1000  # round up to the candle grid
        out = []
        while cursor <= min(end_ms, ms(self.clock.now())):
            moment = datetime.fromtimestamp(cursor / 1000, tz=UTC)
            if moment not in self.missing:
                out.append(moment)
            cursor += step * 1000
        return out

    def _close(self, moment: datetime) -> float:
        return self.close_override.get(moment, 100.0 + (moment - T0).total_seconds() / HOUR)

    def _envelope(self, result: dict[str, Any]) -> Envelope:
        body = json.dumps(result).encode()
        return Envelope(
            result=result,
            body_sha256=hashlib.sha256(body).hexdigest(),
            received_at=self.clock.now(),
            latency_seconds=0.1,
            server_time=None,
            clock_offset_seconds=None,
        )

    async def get_tradingview_chart_data(
        self, instrument_name: str, step_seconds: int, start_ms: int, end_ms: int
    ) -> Envelope:
        self.chart_calls.append((instrument_name, step_seconds, start_ms, end_ms))
        if instrument_name in self.broken:
            raise RuntimeError("exchange exploded")
        starts = self._starts(step_seconds, start_ms, end_ms)
        if not starts:
            return self._envelope({"status": "no_data"})
        closes = [self._close(s) for s in starts]
        result: dict[str, Any] = {
            "status": "ok",
            "ticks": [ms(s) for s in starts],
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1.0] * len(starts),
        }
        if self.extra_field:
            result["brand_new"] = 1
        return self._envelope(result)

    async def get_volatility_index_data(
        self, currency: str, step_seconds: int, start_ms: int, end_ms: int
    ) -> Envelope:
        self.vol_calls.append((start_ms, end_ms))
        starts = self._starts(step_seconds, start_ms, end_ms)
        page = starts[-self.vol_page_size :]
        older = len(starts) > len(page)
        data = [
            [ms(s), self._close(s), self._close(s), self._close(s), self._close(s)] for s in page
        ]
        return self._envelope({"data": data, "continuation": ms(page[0]) if older else None})


class Rig:
    def __init__(self, tmp_path: Path, **settings: Any) -> None:
        self.clock = ManualClock(T0 + timedelta(hours=30))
        self.exchange = FakeExchange(self.clock)
        defaults: dict[str, Any] = {
            "instruments": ("A", "B"),
            "step_seconds": (HOUR,),
            "history_start": T0,
            "max_candles_per_request": 10,
        }
        self.store = BarStore(tmp_path)
        self.backfill = Backfill(
            BackfillSettings(**(defaults | settings)), self.exchange, self.store, self.clock
        )


def test_windows_are_contiguous_non_overlapping_and_capped() -> None:
    end = T0 + timedelta(hours=25)  # 26 candles

    windows = plan_windows(T0, end, HOUR, max_candles=10)

    assert windows == [
        (T0, T0 + timedelta(hours=9)),
        (T0 + timedelta(hours=10), T0 + timedelta(hours=19)),
        (T0 + timedelta(hours=20), end),
    ]
    assert plan_windows(T0, T0, HOUR, 10) == [(T0, T0)]
    assert plan_windows(end, T0, HOUR, 10) == []


async def test_a_first_run_fetches_everything_and_reports_coverage(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    reports = await rig.backfill.run_once()

    by_name = {r.instrument: r for r in reports}
    assert set(by_name) == {"A", "B", "DVOL-BTC"}
    for name in ("A", "B", "DVOL-BTC"):
        report = by_name[name]
        assert report.candles == 31 and report.gaps == 0 and report.error is None
        assert report.first == T0 and report.last == T0 + timedelta(hours=30)
    assert len(rig.exchange.chart_calls) == 8  # 31 candles at 10 per window, for two instruments
    assert rig.store.read("A", HOUR)[5].close == 105.0
    assert rig.store.read("A", HOUR)[0].volume == 1.0
    assert rig.store.read("DVOL-BTC", HOUR)[0].volume is None


async def test_a_rerun_resumes_two_candles_before_the_last_stored_one(tmp_path: Path) -> None:
    rig = Rig(tmp_path, instruments=("A",))
    await rig.backfill.run_once()
    rig.exchange.chart_calls.clear()
    rig.clock.advance(5 * HOUR)

    reports = await rig.backfill.run_once()

    assert reports[0].candles == 36
    first_request_start = rig.exchange.chart_calls[0][2]
    assert first_request_start == ms(T0 + timedelta(hours=28))  # last stored 30h, minus 2 candles
    assert reports[0].changed == 5  # only the five new candles differ from what was stored


async def test_a_still_forming_candle_is_replaced_on_the_next_pass(tmp_path: Path) -> None:
    rig = Rig(tmp_path, instruments=("A",))
    await rig.backfill.run_once()
    last = T0 + timedelta(hours=30)
    rig.exchange.close_override[last] = 777.0

    reports = await rig.backfill.run_once()

    assert reports[0].changed == 1
    assert rig.store.read("A", HOUR)[-1].close == 777.0


async def test_a_series_that_starts_later_is_stored_from_its_first_candle(tmp_path: Path) -> None:
    rig = Rig(tmp_path, instruments=("A",))
    rig.exchange.available_from = T0 + timedelta(hours=12)

    report = (await rig.backfill.run_once())[0]

    assert report.first == T0 + timedelta(hours=12) and report.candles == 19 and report.gaps == 0


async def test_holes_in_the_source_are_reported_not_filled(tmp_path: Path) -> None:
    rig = Rig(tmp_path, instruments=("A",))
    rig.exchange.missing = {T0 + timedelta(hours=7), T0 + timedelta(hours=8)}

    report = (await rig.backfill.run_once())[0]

    assert report.candles == 29 and report.gaps == 1 and report.missing == 2


async def test_dvol_follows_the_continuation_back_through_history(tmp_path: Path) -> None:
    rig = Rig(tmp_path, instruments=(), step_seconds=(HOUR,))
    rig.exchange.vol_page_size = 12  # 31 candles need three pages

    report = (await rig.backfill.run_once())[0]

    assert report.instrument == "DVOL-BTC" and report.candles == 31 and report.gaps == 0
    assert len(rig.exchange.vol_calls) == 3
    ends = [end for _, end in rig.exchange.vol_calls]
    assert ends == sorted(ends, reverse=True)  # each page reaches further back


async def test_one_failing_series_does_not_stop_the_others(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.exchange.broken = {"B"}

    reports = {r.instrument: r for r in await rig.backfill.run_once()}

    assert reports["B"].error and "exchange exploded" in reports["B"].error
    assert reports["A"].error is None and reports["A"].candles == 31
    assert reports["DVOL-BTC"].candles == 31


async def test_new_response_fields_are_reported(tmp_path: Path) -> None:
    rig = Rig(tmp_path, instruments=("A",))
    rig.exchange.extra_field = True

    report = (await rig.backfill.run_once())[0]

    assert report.unexpected_fields == {"brand_new"} and report.candles == 31
