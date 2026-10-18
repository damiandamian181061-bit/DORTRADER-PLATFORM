from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app, get_clock, get_data_dir
from dortrader.clock import ManualClock
from dortrader.storage.bars import BarStore
from dortrader.storage.layout import bars_dir, events_dir
from dortrader.storage.parquet_archive import ParquetArchive
from dortrader.storage.tables import EVENTS_SCHEMA, EVENTS_VERSION, event_row
from tests.helpers import synthetic_hourly_bars

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
FIRST = NOW - timedelta(days=100)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app.dependency_overrides[get_data_dir] = lambda: tmp_path
    app.dependency_overrides[get_clock] = lambda: ManualClock(NOW)
    yield TestClient(app)
    app.dependency_overrides.clear()


def write_events(data_dir: Path, events: list[tuple[datetime, str, dict[str, object]]]) -> None:
    archive = ParquetArchive(
        events_dir(data_dir),
        EVENTS_SCHEMA,
        schema_version=EVENTS_VERSION,
        clock=ManualClock(NOW),
        run_id="t",
        flush_seconds=0.0,
    )
    for at, kind, detail in events:
        archive.append(event_row(at, "run-1", kind, detail), ts=at)
    archive.close()


def test_coverage_lists_every_stored_series_with_its_holes(
    tmp_path: Path, client: TestClient
) -> None:
    store = BarStore(bars_dir(tmp_path))
    bars = synthetic_hourly_bars("BTC_USDC-PERPETUAL", 200, first=FIRST)
    store.upsert([b for b in bars if b.start != FIRST + timedelta(hours=50)])
    store.upsert(synthetic_hourly_bars("BTC-PERPETUAL", 100, first=FIRST, seed=2))

    rows = {
        (r["instrument"], r["step_seconds"]): r for r in client.get("/api/bars/coverage").json()
    }

    usdc = rows[("BTC_USDC-PERPETUAL", 3600)]
    assert (usdc["candles"], usdc["gaps"], usdc["missing"]) == (199, 1, 1)
    assert usdc["first"] == "2026-07-10T12:00:00Z" and usdc["last"].endswith("Z")
    assert rows[("BTC-PERPETUAL", 3600)]["gaps"] == 0 and len(rows) == 2


def test_coverage_is_empty_before_any_backfill(client: TestClient) -> None:
    assert client.get("/api/bars/coverage").json() == []


def test_bars_returns_the_newest_candles_sorted_and_ending_on_the_last(
    tmp_path: Path, client: TestClient
) -> None:
    BarStore(bars_dir(tmp_path)).upsert(synthetic_hourly_bars("X", 500, first=FIRST))

    body = client.get("/api/bars", params={"instrument": "X", "days": 5}).json()

    times = [datetime.fromisoformat(p["time"]) for p in body["points"]]
    assert body["stride"] == 1 and len(times) == 5 * 24 + 1  # five days back from the last candle
    assert times == sorted(times) and times[-1] == FIRST + timedelta(hours=499)
    point = body["points"][0]
    assert point["high"] >= point["low"] and set(point) >= {"open", "close", "volume"}


def test_a_long_range_is_thinned_but_still_ends_on_the_newest_candle(
    tmp_path: Path, client: TestClient
) -> None:
    BarStore(bars_dir(tmp_path)).upsert(synthetic_hourly_bars("X", 7000, first=FIRST))

    body = client.get("/api/bars", params={"instrument": "X", "days": 400}).json()

    assert body["stride"] == 3 and len(body["points"]) <= 3001
    assert datetime.fromisoformat(body["points"][-1]["time"]) == FIRST + timedelta(hours=6999)


@pytest.mark.parametrize(
    ("params", "status"),
    [({"instrument": "NOPE"}, 404), ({"days": 0}, 422), ({"step_seconds": 5}, 422)],
)
def test_bars_errors_are_clear(client: TestClient, params: dict[str, object], status: int) -> None:
    response = client.get("/api/bars", params=params)

    assert response.status_code == status and response.json()["detail"]


def test_events_come_newest_first_and_can_be_filtered(tmp_path: Path, client: TestClient) -> None:
    write_events(
        tmp_path,
        [
            (NOW - timedelta(hours=5), "gap", {"seconds": 400.0}),
            (NOW - timedelta(hours=3), "error", {"where": "cycle", "error": "boom"}),
            (NOW - timedelta(hours=1), "catalog_change", {"added": ["A"], "removed": []}),
            (NOW - timedelta(days=30), "gap", {"seconds": 99.0}),  # older than the window
        ],
    )

    everything = client.get("/api/events").json()
    gaps = client.get("/api/events", params={"kind": "gap"}).json()
    limited = client.get("/api/events", params={"limit": 2}).json()

    assert [e["kind"] for e in everything] == ["catalog_change", "error", "gap"]
    assert everything[1]["detail"] == {"where": "cycle", "error": "boom"}
    assert len(gaps) == 1 and gaps[0]["detail"]["seconds"] == 400.0
    assert [e["kind"] for e in limited] == ["catalog_change", "error"]
    assert len(client.get("/api/events", params={"days": 90}).json()) == 4


def test_events_without_any_recording_are_an_empty_list_and_bad_kinds_are_rejected(
    client: TestClient,
) -> None:
    assert client.get("/api/events").json() == []
    assert client.get("/api/events", params={"kind": "nonsense"}).status_code == 422
