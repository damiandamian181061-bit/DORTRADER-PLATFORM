from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from apps.api import vol_view
from apps.api.main import app, get_clock, get_data_dir
from dortrader.clock import ManualClock
from dortrader.forecasting import EvaluationReport, NaiveLast, evaluate
from dortrader.storage.bars import BarStore
from dortrader.storage.layout import bars_dir
from tests.helpers import synthetic_hourly_bars

COUNT = 6000  # about 250 days of hourly candles
FIRST = datetime(2026, 1, 1, tzinfo=UTC)
INSTRUMENT = "BTC_USDC-PERPETUAL"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    vol_view._candle_cache.clear()
    vol_view._forecast_cache.clear()
    app.dependency_overrides[get_data_dir] = lambda: tmp_path
    app.dependency_overrides[get_clock] = lambda: ManualClock(FIRST)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    store = BarStore(bars_dir(tmp_path))
    store.upsert(synthetic_hourly_bars(INSTRUMENT, COUNT, first=FIRST, seed=1))
    dvol = [
        b.model_copy(update={"instrument": "DVOL-BTC", "volume": None, "open": 50.0, "high": 51.0,
                             "low": 49.0, "close": 50.0})
        for b in synthetic_hourly_bars("x", COUNT, first=FIRST, seed=2)
    ]  # fmt: skip
    store.upsert(dvol)
    return tmp_path


def test_realized_vol_gives_daily_points_for_each_estimator(
    seeded: Path, client: TestClient
) -> None:
    response = client.get(
        "/api/vol/realized",
        params=[("estimators", "close_to_close"), ("estimators", "parkinson"), ("days", 100)],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["instrument"] == INSTRUMENT and body["window_days"] == 30
    assert [s["estimator"] for s in body["series"]] == ["close_to_close", "parkinson"]
    points = body["series"][0]["points"]
    assert 95 <= len(points) <= 101
    times = [datetime.fromisoformat(p["time"]) for p in points]
    assert all(b - a == timedelta(days=1) for a, b in zip(times, times[1:], strict=False))
    assert all(t.hour == 0 and t.minute == 0 for t in times)
    vols = [p["vol"] for p in points if p["vol"] is not None]
    assert vols and all(0.05 < v < 3.0 for v in vols)
    assert len(body["dvol"]) == len(points) and body["dvol"][0]["vol"] == pytest.approx(0.5)


def test_realized_vol_defaults_and_validation(seeded: Path, client: TestClient) -> None:
    default = client.get("/api/vol/realized").json()

    assert [s["estimator"] for s in default["series"]] == ["close_to_close", "yang_zhang"]
    assert client.get("/api/vol/realized", params={"estimators": "nope"}).status_code == 422
    assert client.get("/api/vol/realized", params={"window_days": 1}).status_code == 422
    assert client.get("/api/vol/realized", params={"window_days": 181}).status_code == 422


def test_unknown_series_and_too_long_windows_are_clear_404s(
    seeded: Path, client: TestClient
) -> None:
    unknown = client.get("/api/vol/realized", params={"instrument": "NOPE"})
    too_long = client.get("/api/vol/realized", params={"window_days": 180, "days": 30})
    assert unknown.status_code == 404 and "backfill" in unknown.json()["detail"]
    assert too_long.status_code == 200  # 180 days fit in 250
    empty_client_response = client.get("/api/vol/forecast", params={"instrument": "NOPE"})
    assert empty_client_response.status_code == 404


def test_without_dvol_the_series_still_works(tmp_path: Path, client: TestClient) -> None:
    BarStore(bars_dir(tmp_path)).upsert(synthetic_hourly_bars(INSTRUMENT, COUNT, first=FIRST))

    body = client.get("/api/vol/realized").json()

    assert body["dvol"] == [] and body["series"][0]["points"]


def test_forecast_lists_every_model_at_three_horizons(seeded: Path, client: TestClient) -> None:
    response = client.get("/api/vol/forecast")

    assert response.status_code == 200
    body = response.json()
    assert [h["horizon_hours"] for h in body["horizons"]] == [24, 168, 720]
    expected = {
        "naive",
        "trailing_30d",
        "ewma_riskmetrics",
        "ewma_fitted",
        "garch",
        "gjr_garch",
        "har",
    }
    for horizon in body["horizons"]:
        assert {f["model"] for f in horizon["forecasts"]} == expected
        assert all(0.05 < f["vol"] < 5.0 for f in horizon["forecasts"])
    last = datetime.fromisoformat(body["as_of"])
    assert last == FIRST + timedelta(hours=COUNT)  # the close of the last candle
    assert [r["window_hours"] for r in body["last_realized"]] == [24, 168, 720]
    assert body["dvol"] == pytest.approx(0.5)


def test_the_naive_forecast_equals_the_last_realized_vol(seeded: Path, client: TestClient) -> None:
    body = client.get("/api/vol/forecast").json()

    naive = {
        h["horizon_hours"]: next(f["vol"] for f in h["forecasts"] if f["model"] == "naive")
        for h in body["horizons"]
    }
    last = {r["window_hours"]: r["vol"] for r in body["last_realized"]}
    assert naive == pytest.approx(last)


def test_a_repeated_forecast_request_is_identical_and_served_from_cache(
    seeded: Path, client: TestClient
) -> None:
    first = client.get("/api/vol/forecast")
    assert len(vol_view._forecast_cache) == 1

    second = client.get("/api/vol/forecast")

    assert first.content == second.content


def test_too_little_history_is_a_clear_404(tmp_path: Path, client: TestClient) -> None:
    BarStore(bars_dir(tmp_path)).upsert(synthetic_hourly_bars(INSTRUMENT, 900, first=FIRST))

    response = client.get("/api/vol/forecast")

    assert response.status_code == 404 and "not enough history" in response.json()["detail"]


def test_evaluation_serves_the_stored_report_or_explains_how_to_make_one(
    tmp_path: Path, client: TestClient
) -> None:
    missing = client.get("/api/vol/evaluation")
    assert missing.status_code == 404 and "evaluate_vol_forecasts" in missing.json()["detail"]

    returns = np.random.default_rng(0).standard_normal(3000) * 0.005
    known = (np.arange(3000, dtype=np.int64) + 1) * 3_600_000_000
    report = evaluate(
        returns,
        known,
        series="TEST",
        horizons=[24],
        test_start_index=1500,
        model_factory=lambda: [NaiveLast()],
    )
    folder = tmp_path / "evaluation"
    folder.mkdir()
    (folder / "vol_forecasts.json").write_text(report.model_dump_json(), encoding="utf-8")

    served = client.get("/api/vol/evaluation")

    assert served.status_code == 200
    assert EvaluationReport.model_validate(served.json()) == report
