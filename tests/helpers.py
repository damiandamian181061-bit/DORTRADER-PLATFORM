"""Shared test doubles: a scripted Deribit client and a recorder rig on a manual clock."""

import copy
import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from apps.worker.recorder import Recorder
from dortrader.clock import ManualClock
from dortrader.config import RecorderSettings
from dortrader.domain import Bar, us_to_utc
from dortrader.market_data.deribit.rest import Envelope
from dortrader.storage.layout import events_dir
from dortrader.storage.parquet_archive import read_archive
from dortrader.storage.tables import EVENTS_VERSION
from tests.conftest import load_json

RAW_INSTRUMENTS = load_json("deribit", "instruments_usdc_option.json")
RAW_SUMMARY = load_json("deribit", "book_summary_usdc_option.json")
START = us_to_utc(RAW_SUMMARY["usOut"])


class FakeClient:
    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock
        self.instruments = copy.deepcopy(RAW_INSTRUMENTS["result"])
        self.summary = copy.deepcopy(RAW_SUMMARY["result"])
        self.instrument_calls = 0
        self.summary_calls = 0
        self.index_calls = 0
        self.summary_error: Exception | None = None
        self.summary_failures = 0  # fail this many calls with summary_error, then recover
        self.clock_offset: float | None = 0.01

    def _envelope(self, result: Any, offset: float | None = None) -> Envelope:
        body = json.dumps(result).encode()
        return Envelope(
            result=result,
            body_sha256=hashlib.sha256(body).hexdigest(),
            received_at=self.clock.now(),
            latency_seconds=0.2,
            server_time=self.clock.now(),
            clock_offset_seconds=offset,
        )

    async def get_instruments(self, currency: str, kind: str) -> Envelope:
        self.instrument_calls += 1
        return self._envelope(self.instruments)

    async def get_book_summary_by_currency(self, currency: str, kind: str) -> Envelope:
        self.summary_calls += 1
        if self.summary_error and self.summary_failures > 0:
            self.summary_failures -= 1
            raise self.summary_error
        # The summary's own offset is stale (server-side cache) and must be ignored.
        return self._envelope(self.summary, offset=1.5)

    async def get_index_price(self, index_name: str) -> Envelope:
        self.index_calls += 1
        return self._envelope({"index_price": 81000.0}, offset=self.clock_offset)


class Rig:
    def __init__(self, tmp_path: Path, **settings: Any) -> None:
        self.clock = ManualClock(START)
        self.client = FakeClient(self.clock)
        self.settings = RecorderSettings(data_dir=tmp_path, **settings)
        self.recorder = Recorder(
            self.settings, self.client, self.clock, run_id="test", sleep=self.sleep
        )
        self.root = tmp_path

    async def sleep(self, seconds: float) -> None:
        self.clock.advance(seconds)

    def events(self) -> list[dict[str, Any]]:
        self.recorder.close()
        table = read_archive(events_dir(self.root), schema_version=EVENTS_VERSION)
        return [{**e, "detail": json.loads(e["detail"])} for e in table.to_pylist()]


def synthetic_hourly_bars(
    instrument: str,
    count: int,
    *,
    first: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    annual_vol: float = 0.5,
    seed: int = 0,
) -> list[Bar]:
    """Hourly candles from a random walk with a calm stretch followed by a stormy one."""
    rng = np.random.default_rng(seed)
    scale = np.where(np.arange(count) < count * 0.8, 1.0, 2.0) * annual_vol / math.sqrt(8760)
    closes = 80_000 * np.exp(np.cumsum(rng.standard_normal(count) * scale))
    previous, bars = 80_000.0, []
    for i, close in enumerate(closes):
        wiggle = abs(rng.standard_normal()) * scale[i] * close
        bars.append(
            Bar(
                instrument=instrument,
                step_seconds=3600,
                start=first + timedelta(hours=i),
                open=previous,
                high=max(previous, close) + wiggle,
                low=min(previous, close) - wiggle,
                close=float(close),
                volume=1.0,
            )
        )
        previous = float(close)
    return bars
