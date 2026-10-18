import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query

from apps.api.chain_view import build_chain_response
from apps.api.data_view import (
    NoBars,
    build_bars,
    build_coverage,
    instrument_view,
    read_events,
)
from apps.api.price_view import PriceRequest, PriceResponse, calculate
from apps.api.schemas import (
    BarsResponse,
    ChainResponse,
    EventView,
    ForecastResponse,
    GapView,
    InstrumentView,
    RealizedVolResponse,
    SeriesCoverage,
    StatusResponse,
)
from apps.api.vol_view import (
    DVOL,
    NoSeries,
    build_realized_response,
    cached_forecast,
    load_candles,
)
from dortrader.clock import Clock, SystemClock
from dortrader.config import get_settings
from dortrader.domain import ChainSnapshot
from dortrader.forecasting import EvaluationReport
from dortrader.replay import ChainStore, NoRecordedData
from dortrader.storage.bars import BarStore
from dortrader.storage.heartbeat import read_heartbeat
from dortrader.storage.layout import bars_dir, events_dir
from dortrader.storage.parquet_archive import NoDataError, read_archive
from dortrader.storage.tables import EVENTS_VERSION
from dortrader.volatility.realized import Estimator

app = FastAPI(title="DORTRADER API")


def get_data_dir() -> Path:
    return get_settings().recorder.data_dir


def get_clock() -> Clock:
    return SystemClock()


DataDir = Annotated[Path, Depends(get_data_dir)]
Now = Annotated[Clock, Depends(get_clock)]


def utc_or_422(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise HTTPException(422, "`at` needs a UTC offset, e.g. 2026-10-18T12:00:00Z")
    return value.astimezone(UTC) if value is not None else None


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
def status(data_dir: DataDir, clock: Now) -> StatusResponse:
    now = clock.now()
    heartbeat = read_heartbeat(data_dir)
    bounds = ChainStore(data_dir).recorded_range()
    return StatusResponse(
        now=now,
        healthy=heartbeat is not None and not heartbeat.is_stale(now),
        last_good_at=heartbeat.last_good_at if heartbeat else None,
        age_seconds=heartbeat.age_seconds(now) if heartbeat else None,
        quotes_per_cycle=heartbeat.quotes if heartbeat else None,
        run_id=heartbeat.run_id if heartbeat else None,
        recorded_from=bounds[0] if bounds else None,
        recorded_to=bounds[1] if bounds else None,
        recent_gaps=_recent_gaps(data_dir, now),
    )


def _recent_gaps(data_dir: Path, now: datetime, days: int = 7, limit: int = 20) -> list[GapView]:
    try:
        table = read_archive(
            events_dir(data_dir),
            schema_version=EVENTS_VERSION,
            start=now - timedelta(days=days),
            end=now,
        )
    except NoDataError:
        return []
    gaps = []
    for event in table.sort_by("at").to_pylist():
        if event["kind"] == "gap":
            detail = json.loads(event["detail"])
            gaps.append(
                GapView(
                    start=datetime.fromisoformat(detail["start"]),
                    end=datetime.fromisoformat(detail["end"]),
                    seconds=detail["seconds"],
                )
            )
    return gaps[-limit:]


def _snapshot(data_dir: Path, at: datetime | None) -> ChainSnapshot:
    store = ChainStore(data_dir)
    when = utc_or_422(at)
    if when is None:
        bounds = store.recorded_range()
        if bounds is None:
            raise HTTPException(404, "nothing has been recorded yet")
        when = bounds[1]
    try:
        return store.as_of(when)
    except NoRecordedData as error:
        raise HTTPException(404, str(error)) from error


@app.get("/api/chain")
def chain(
    data_dir: DataDir,
    at: Annotated[
        datetime | None, Query(description="UTC time; default is the latest recorded")
    ] = None,
    expiries: Annotated[int, Query(ge=1, le=20)] = 3,
) -> ChainResponse:
    return build_chain_response(_snapshot(data_dir, at), expiries)


@app.get("/api/instruments")
def instruments(
    data_dir: DataDir, at: Annotated[datetime | None, Query()] = None
) -> list[InstrumentView]:
    return [instrument_view(row.instrument) for row in _snapshot(data_dir, at).rows]


def _candles(data_dir: Path, instrument: str):  # type: ignore[no-untyped-def]
    store = BarStore(bars_dir(data_dir))
    try:
        return load_candles(store, data_dir, instrument), store
    except NoSeries as error:
        raise HTTPException(404, str(error)) from error


def _dvol(store: BarStore, data_dir: Path):  # type: ignore[no-untyped-def]
    try:
        return load_candles(store, data_dir, DVOL)
    except NoSeries:
        return None


@app.get("/api/vol/realized")
def realized_vol(
    data_dir: DataDir,
    instrument: Annotated[str, Query()] = "BTC_USDC-PERPETUAL",
    estimators: Annotated[list[Estimator] | None, Query()] = None,
    window_days: Annotated[int, Query(ge=2, le=180)] = 30,
    days: Annotated[int, Query(ge=1, le=2000)] = 365,
) -> RealizedVolResponse:
    candles, store = _candles(data_dir, instrument)
    try:
        return build_realized_response(
            candles,
            _dvol(store, data_dir),
            instrument=instrument,
            estimators=estimators or [Estimator.CLOSE_TO_CLOSE, Estimator.YANG_ZHANG],
            window_days=window_days,
            days=days,
        )
    except NoSeries as error:
        raise HTTPException(404, str(error)) from error


@app.get("/api/vol/forecast")
def vol_forecast(
    data_dir: DataDir, instrument: Annotated[str, Query()] = "BTC_USDC-PERPETUAL"
) -> ForecastResponse:
    candles, store = _candles(data_dir, instrument)
    try:
        return cached_forecast(candles, _dvol(store, data_dir), instrument=instrument)
    except NoSeries as error:
        raise HTTPException(404, str(error)) from error


@app.get("/api/vol/evaluation")
def vol_evaluation(data_dir: DataDir) -> EvaluationReport:
    path = data_dir / "evaluation" / "vol_forecasts.json"
    try:
        return EvaluationReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise HTTPException(
            404, "no forecast evaluation yet; run scripts/evaluate_vol_forecasts.py"
        ) from error


@app.post("/api/price")
def price(request: PriceRequest) -> PriceResponse:
    return calculate(request)


@app.get("/api/bars/coverage")
def bars_coverage(data_dir: DataDir) -> list[SeriesCoverage]:
    return build_coverage(BarStore(bars_dir(data_dir)))


@app.get("/api/bars")
def bars(
    data_dir: DataDir,
    instrument: Annotated[str, Query()] = "BTC_USDC-PERPETUAL",
    step_seconds: Annotated[int, Query(ge=60)] = 3600,
    days: Annotated[int, Query(ge=1, le=4000)] = 90,
) -> BarsResponse:
    try:
        return build_bars(BarStore(bars_dir(data_dir)), instrument, step_seconds, days)
    except NoBars as error:
        raise HTTPException(404, str(error)) from error


@app.get("/api/events")
def events(
    data_dir: DataDir,
    clock: Now,
    kind: Annotated[
        Literal["gap", "error", "schema_drift", "catalog_change"] | None, Query()
    ] = None,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[EventView]:
    return read_events(data_dir, clock.now(), days=days, kind=kind, limit=limit)
