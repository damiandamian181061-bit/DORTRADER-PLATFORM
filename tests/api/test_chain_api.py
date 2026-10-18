import asyncio
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app, get_clock, get_data_dir
from dortrader.clock import ManualClock
from tests.helpers import START, Rig


def record(rig: Rig, cycles: int = 1, step: float = 30.0) -> None:
    async def run() -> None:
        for _ in range(cycles):
            await rig.recorder.run_cycle()
            rig.clock.advance(step)
        rig.recorder.close()

    asyncio.run(run())


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app.dependency_overrides[get_data_dir] = lambda: tmp_path
    app.dependency_overrides[get_clock] = lambda: ManualClock(START + timedelta(seconds=10))
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_chain_defaults_to_the_latest_recording_and_groups_by_expiry_and_strike(
    rig: Rig, client: TestClient
) -> None:
    record(rig)

    response = client.get("/api/chain")

    assert response.status_code == 200
    body = response.json()
    assert len(body["expiries"]) == 2  # the fixture holds two expiries; default asks for 3
    expiry = body["expiries"][0]
    strikes = [s["strike"] for s in expiry["strikes"]]
    assert strikes == sorted(strikes) and len(strikes) > 20
    both = next(s for s in expiry["strikes"] if s["call"] and s["put"])
    call = both["call"]
    assert call["instrument_name"].endswith("-C") and both["put"]["instrument_name"].endswith("-P")
    assert call["mark_iv"] is not None and call["delta"] is not None
    assert expiry["forward"] > 70_000 and body["model_version"] == "black76-1"
    assert body["snapshot_at"].endswith("Z")


def test_expiry_count_limits_the_response(rig: Rig, client: TestClient) -> None:
    record(rig)

    body = client.get("/api/chain", params={"expiries": 1}).json()

    assert len(body["expiries"]) == 1


def test_the_same_request_returns_identical_json(rig: Rig, client: TestClient) -> None:
    record(rig, cycles=2)

    first, second = client.get("/api/chain"), client.get("/api/chain")

    assert first.status_code == 200 and first.content == second.content


def test_an_explicit_time_returns_that_moment_not_the_latest(rig: Rig, client: TestClient) -> None:
    record(rig, cycles=3)
    first_cycle = START.isoformat().replace("+00:00", "Z")

    body = client.get("/api/chain", params={"at": first_cycle}).json()

    assert body["snapshot_at"] == first_cycle
    latest = client.get("/api/chain").json()
    assert latest["snapshot_at"] != body["snapshot_at"]


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({"at": "2026-10-18T19:17:39"}, 422),  # no UTC offset
        ({"at": "not a time"}, 422),
        ({"expiries": 0}, 422),
        ({"expiries": 21}, 422),
        ({"at": "2026-10-17T00:00:00Z"}, 404),  # before anything was recorded
    ],
)
def test_bad_or_unrecorded_requests_get_clear_errors(
    rig: Rig, client: TestClient, params: dict[str, object], status: int
) -> None:
    record(rig)

    response = client.get("/api/chain", params=params)

    assert response.status_code == status
    assert response.json()["detail"]


def test_an_empty_data_directory_is_a_404_not_a_crash(client: TestClient) -> None:
    response = client.get("/api/chain")

    assert response.status_code == 404 and "nothing has been recorded" in response.json()["detail"]


def test_instruments_lists_what_was_listed_at_that_time(rig: Rig, client: TestClient) -> None:
    record(rig)

    body = client.get("/api/instruments").json()

    assert len(body) == 88 and body[0]["name"].startswith("BTC_USDC-")
    first = body[0]  # plain numbers, not exact-decimal strings
    assert isinstance(first["strike"], float) and first["tick_size"] == 5.0
    assert first["tick_size_steps"] == [{"above_price": 1000.0, "tick_size": 20.0}]
    assert first["min_trade_amount"] == 0.01 and first["contract_size"] == 1.0


def test_status_is_healthy_right_after_a_cycle_and_unhealthy_when_stale(
    rig: Rig, client: TestClient
) -> None:
    record(rig, cycles=2)
    fresh = client.get("/api/status").json()

    app.dependency_overrides[get_clock] = lambda: ManualClock(START + timedelta(hours=1))
    stale = client.get("/api/status").json()

    assert fresh["healthy"] is True and fresh["quotes_per_cycle"] == 88
    assert fresh["recorded_from"] and fresh["recorded_to"] and fresh["recent_gaps"] == []
    assert stale["healthy"] is False and stale["age_seconds"] > 3000


def test_status_without_any_recording_is_unhealthy_with_nothing_to_report(
    client: TestClient,
) -> None:
    body = client.get("/api/status").json()

    assert body["healthy"] is False and body["last_good_at"] is None
    assert body["recorded_from"] is None and body["recent_gaps"] == []


def test_status_reports_recording_gaps(rig: Rig, client: TestClient) -> None:
    async def run() -> None:
        await rig.recorder.run_cycle()
        rig.clock.advance(400)
        await rig.recorder.run_cycle()
        rig.recorder.close()

    asyncio.run(run())

    gaps = client.get("/api/status").json()["recent_gaps"]

    assert len(gaps) == 1 and gaps[0]["seconds"] == pytest.approx(400)
