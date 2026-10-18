"""What has been collected: candle coverage, candles for charting, and the recorder's event log."""

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

from apps.api.schemas import (
    BarPoint,
    BarsResponse,
    EventView,
    InstrumentView,
    SeriesCoverage,
    TickStepView,
)
from dortrader.domain import Instrument
from dortrader.storage.bars import BarStore
from dortrader.storage.layout import events_dir
from dortrader.storage.parquet_archive import NoDataError, read_archive
from dortrader.storage.tables import EVENTS_VERSION

MAX_POINTS = 3000


class NoBars(Exception):
    """The requested candle series is not stored."""


def build_coverage(store: BarStore) -> list[SeriesCoverage]:
    rows = []
    for instrument, step in store.series():
        coverage = store.coverage(instrument, step)
        if coverage is not None:
            rows.append(
                SeriesCoverage(
                    instrument=instrument,
                    step_seconds=step,
                    first=coverage.first,
                    last=coverage.last,
                    candles=coverage.candles,
                    gaps=coverage.gaps,
                    missing=coverage.missing,
                )
            )
    return rows


def build_bars(store: BarStore, instrument: str, step_seconds: int, days: int) -> BarsResponse:
    bounds = store.bounds(instrument, step_seconds)
    if bounds is None:
        raise NoBars(f"no candles stored for {instrument} at {step_seconds} seconds")
    bars = store.read(instrument, step_seconds, start=bounds[1] - timedelta(days=days))
    stride = max(1, math.ceil(len(bars) / MAX_POINTS))
    chosen = bars[::stride]
    if chosen[-1] is not bars[-1]:
        chosen.append(bars[-1])  # always end on the newest candle
    return BarsResponse(
        instrument=instrument,
        step_seconds=step_seconds,
        stride=stride,
        points=[
            BarPoint(
                time=b.start, open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume
            )
            for b in chosen
        ],
    )


def read_events(
    data_dir: Path, now: datetime, *, days: int, kind: str | None, limit: int
) -> list[EventView]:
    """Recorder events, newest first: gaps, errors, schema drift and catalog changes."""
    try:
        table = read_archive(
            events_dir(data_dir),
            schema_version=EVENTS_VERSION,
            start=now - timedelta(days=days),
            end=now,
        )
    except NoDataError:
        return []
    rows = table.sort_by([("at", "descending")]).to_pylist()
    events = [
        EventView(at=r["at"], run_id=r["run_id"], kind=r["kind"], detail=json.loads(r["detail"]))
        for r in rows
        if kind is None or r["kind"] == kind
    ]
    return events[:limit]


def instrument_view(i: Instrument) -> InstrumentView:
    return InstrumentView(
        instrument_id=i.instrument_id,
        name=i.name,
        option_type=i.option_type,
        strike=float(i.strike),
        expiration=i.expiration,
        created_at=i.created_at,
        contract_size=float(i.contract_size),
        tick_size=float(i.tick_size),
        tick_size_steps=[
            TickStepView(above_price=float(s.above_price), tick_size=float(s.tick_size))
            for s in i.tick_size_steps
        ],
        min_trade_amount=float(i.min_trade_amount),
        state=i.state,
        is_active=i.is_active,
        maker_commission=None if i.maker_commission is None else float(i.maker_commission),
        taker_commission=None if i.taker_commission is None else float(i.taker_commission),
    )
