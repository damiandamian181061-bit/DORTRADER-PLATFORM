"""Docker healthcheck: exit 0 if the recorder wrote a heartbeat recently, 1 otherwise.

Run:  python -m apps.worker.healthcheck
"""

import sys

from dortrader.clock import Clock, SystemClock
from dortrader.config import RecorderSettings, get_settings
from dortrader.storage.heartbeat import read_heartbeat


def check(settings: RecorderSettings, clock: Clock) -> tuple[bool, str]:
    heartbeat = read_heartbeat(settings.data_dir)
    if heartbeat is None:
        return False, "no heartbeat"
    age = heartbeat.age_seconds(clock.now())
    if heartbeat.is_stale(clock.now()):
        return False, f"last good cycle {age:.0f}s ago"
    return True, f"ok, last good cycle {age:.0f}s ago"


def main() -> None:
    healthy, message = check(get_settings().recorder, SystemClock())
    print(message)
    sys.exit(0 if healthy else 1)


if __name__ == "__main__":
    main()
