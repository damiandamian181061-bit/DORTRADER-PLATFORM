from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apps.worker.healthcheck import check
from dortrader.clock import ManualClock
from dortrader.config import RecorderSettings
from dortrader.storage.heartbeat import HEARTBEAT_FILE, Heartbeat, read_heartbeat, write_heartbeat

T0 = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def beat(last_good: datetime = T0, interval: float = 30.0) -> Heartbeat:
    return Heartbeat(
        run_id="r",
        last_good_at=last_good,
        written_at=last_good,
        quotes=596,
        poll_interval_seconds=interval,
    )


def test_round_trip_and_staleness(tmp_path: Path) -> None:
    write_heartbeat(tmp_path, beat())

    loaded = read_heartbeat(tmp_path)

    assert loaded == beat()
    assert not loaded.is_stale(T0 + timedelta(seconds=89))  # within 3 intervals
    assert loaded.is_stale(T0 + timedelta(seconds=91))
    assert loaded.age_seconds(T0 + timedelta(seconds=10)) == 10


def test_missing_or_corrupt_heartbeat_reads_as_none(tmp_path: Path) -> None:
    assert read_heartbeat(tmp_path) is None
    (tmp_path / HEARTBEAT_FILE).write_text("{not json", encoding="utf-8")
    assert read_heartbeat(tmp_path) is None


def test_healthcheck_reports_health_from_the_heartbeat(tmp_path: Path) -> None:
    settings = RecorderSettings(data_dir=tmp_path)
    clock = ManualClock(T0)

    assert check(settings, clock) == (False, "no heartbeat")
    write_heartbeat(tmp_path, beat(T0))
    clock.advance(30)
    assert check(settings, clock)[0] is True
    clock.advance(200)
    healthy, message = check(settings, clock)
    assert healthy is False and "230s ago" in message


@pytest.mark.anyio
async def test_recorder_writes_a_heartbeat_every_cycle(tmp_path: Path) -> None:
    from tests.helpers import Rig

    rig = Rig(tmp_path)

    await rig.recorder.run_cycle()

    heartbeat = read_heartbeat(tmp_path)
    assert heartbeat is not None
    assert heartbeat.run_id == "test" and heartbeat.quotes == 88
    assert heartbeat.last_good_at == rig.clock.now()
