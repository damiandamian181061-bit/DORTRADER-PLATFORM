import json
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr, ValidationError

from dortrader.clock import ManualClock, SystemClock
from dortrader.config import Settings
from dortrader.log import configure_logging, get_logger


def test_defaults_are_public_prod_and_have_no_credentials() -> None:
    settings = Settings(_env_file=None)

    assert settings.deribit.base_url == "https://www.deribit.com/api/v2"
    assert settings.database_url is None
    assert not any("key" in name or "secret" in name for name in Settings.model_fields)


def test_env_overrides_nested_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DORTRADER_DERIBIT__ENVIRONMENT", "test")
    monkeypatch.setenv("DORTRADER_RECORDER__POLL_INTERVAL_SECONDS", "5")

    settings = Settings(_env_file=None)

    assert settings.deribit.base_url == "https://test.deribit.com/api/v2"
    assert settings.recorder.poll_interval_seconds == 5


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DORTRADER_DERIBIT__ENVIRONMENT", "staging"),
        ("DORTRADER_RECORDER__POLL_INTERVAL_SECONDS", "0"),
        ("DORTRADER_LOG_LEVEL", "LOUD"),
    ],
)
def test_invalid_config_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secret_never_appears_in_repr_or_logs(capsys: pytest.CaptureFixture[str]) -> None:
    url = "postgresql://postgres:hunter2@db.example.supabase.co:5432/postgres"
    settings = Settings(_env_file=None, database_url=SecretStr(url))
    configure_logging("INFO", json_logs=True)

    get_logger("test").info(
        f"connecting to {url}",
        database_url=settings.database_url,
        api_key="abc123",
        nested={"password": "hunter2"},
    )
    out = capsys.readouterr().out

    assert "hunter2" not in out and "abc123" not in out
    assert "hunter2" not in repr(settings)
    record = json.loads(out.strip().splitlines()[-1])
    assert record["api_key"] == "***" and record["level"] == "info"


def test_manual_clock_only_moves_when_advanced() -> None:
    clock = ManualClock(datetime(2026, 10, 18, tzinfo=UTC))
    clock.advance(30)

    assert clock.now() == datetime(2026, 10, 18, 0, 0, 30, tzinfo=UTC)
    assert clock.monotonic() == 30
    with pytest.raises(ValueError):
        ManualClock(datetime(2026, 10, 18))


def test_system_clock_is_utc_aware() -> None:
    assert SystemClock().now().tzinfo is UTC
