"""Settings, read from DORTRADER_* environment variables and an optional .env file.

There are deliberately no private exchange credentials here: this phase only reads
public market data. Invalid configuration fails at startup.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_DERIBIT_URLS = {
    "prod": "https://www.deribit.com/api/v2",
    "test": "https://test.deribit.com/api/v2",
}


class DeribitSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment: Literal["prod", "test"] = "prod"
    timeout_seconds: float = Field(default=20.0, gt=0)
    max_retries: int = Field(default=4, ge=0, le=10)
    # Minimum spacing between calls, per method. get_instruments is the expensive one.
    min_interval_seconds: float = Field(default=1.0, ge=0)
    instruments_min_interval_seconds: float = Field(default=60.0, ge=0)

    @property
    def base_url(self) -> str:
        return _DERIBIT_URLS[self.environment]


class RecorderSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    underlying: str = "BTC"
    settlement_currency: str = "USDC"
    poll_interval_seconds: float = Field(default=30.0, gt=0)
    catalog_refresh_seconds: float = Field(default=600.0, gt=0)
    data_dir: Path = Path("data")
    flush_seconds: float = Field(default=300.0, gt=0)
    wide_spread_ratio: float = Field(default=0.5, gt=0)
    stale_exchange_seconds: float = Field(default=60.0, gt=0)
    clock_drift_seconds: float = Field(default=2.0, gt=0)
    # A gap is reported when two good cycles are further apart than this many poll intervals.
    gap_factor: float = Field(default=2.5, gt=1)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DORTRADER_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
        frozen=True,
    )

    deribit: DeribitSettings = DeribitSettings()
    recorder: RecorderSettings = RecorderSettings()
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    json_logs: bool = True
    # Supabase Postgres connection string; unused until the database layer lands.
    database_url: SecretStr | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
