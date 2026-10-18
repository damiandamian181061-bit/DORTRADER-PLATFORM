"""A liveness file the recorder rewrites every cycle; the API and Docker healthcheck read it."""

import os
from datetime import datetime
from pathlib import Path

from dortrader.domain import FrozenModel, UtcDateTime

HEARTBEAT_FILE = "_heartbeat.json"


class Heartbeat(FrozenModel):
    run_id: str
    last_good_at: UtcDateTime  # receipt time of the last successful cycle
    written_at: UtcDateTime
    quotes: int
    poll_interval_seconds: float

    def age_seconds(self, now: datetime) -> float:
        return (now - self.last_good_at).total_seconds()

    def is_stale(self, now: datetime, factor: float = 3.0) -> bool:
        return self.age_seconds(now) > self.poll_interval_seconds * factor


def write_heartbeat(data_dir: Path, heartbeat: Heartbeat) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / HEARTBEAT_FILE
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(heartbeat.model_dump_json(), encoding="utf-8")
    os.replace(temporary, path)


def read_heartbeat(data_dir: Path) -> Heartbeat | None:
    """None when there is no usable heartbeat (missing or unreadable), never an exception."""
    try:
        return Heartbeat.model_validate_json(
            (data_dir / HEARTBEAT_FILE).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
