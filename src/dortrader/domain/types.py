"""Value types shared by every layer. No IO, no project imports."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict

Currency = Literal["BTC", "USDC", "USD"]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


UtcDateTime = Annotated[datetime, AfterValidator(_require_utc)]


class FrozenModel(BaseModel):
    """Base for every domain entity: immutable, no unknown fields, no NaN/inf."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class Money(FrozenModel):
    """An amount with its currency. Ledger and position values use this, never bare floats."""

    amount: Decimal
    currency: Currency


def ms_to_utc(milliseconds: int) -> datetime:
    return _EPOCH + timedelta(milliseconds=milliseconds)


def us_to_utc(microseconds: int) -> datetime:
    return _EPOCH + timedelta(microseconds=microseconds)


def vol_from_percent(percent: float) -> float:
    """Deribit quotes IV in percent (36.5 means 36.5%). Everything inside uses decimals."""
    return percent / 100.0
