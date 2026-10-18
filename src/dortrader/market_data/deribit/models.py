"""Raw Deribit response shapes. Unknown extra fields are kept and reported, never ignored silently.

A field we rely on going missing is an error (SchemaDriftError). A new field only produces
a warning for the caller to log, because exchanges add fields without notice.
"""

from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError


class SchemaDriftError(ValueError):
    """The exchange response no longer has the shape this code depends on."""


class _Raw(BaseModel):
    model_config = ConfigDict(extra="allow")


class RawTickStep(_Raw):
    above_price: Decimal
    tick_size: Decimal


class RawInstrument(_Raw):
    instrument_name: str
    instrument_id: int
    kind: str
    base_currency: str
    quote_currency: str
    settlement_currency: str
    instrument_type: str
    option_type: str
    strike: Decimal
    expiration_timestamp: int
    creation_timestamp: int
    contract_size: Decimal
    tick_size: Decimal
    tick_size_steps: list[RawTickStep] = []
    min_trade_amount: Decimal
    state: str
    is_active: bool
    price_index: str
    maker_commission: Decimal | None = None
    taker_commission: Decimal | None = None


class RawBookSummary(_Raw):
    instrument_name: str
    base_currency: str
    quote_currency: str | None = None
    bid_price: float | None = None
    ask_price: float | None = None
    mark_price: float
    mark_iv: float | None = None
    underlying_price: float | None = None
    underlying_index: str | None = None
    interest_rate: float | None = None
    open_interest: float
    volume: float | None = None
    last: float | None = None
    creation_timestamp: int | None = None


# Every field seen in live responses on 2026-10-18 (see tests/fixtures/deribit).
KNOWN_INSTRUMENT_FIELDS = frozenset(
    {
        *RawInstrument.model_fields,
        "base_currency_uuid",
        "block_trade_commission",
        "block_trade_min_trade_amount",
        "block_trade_tick_size",
        "counter_currency",
        "index_id",
        "lot_size",
        "product_group",
        "quote_currency_uuid",
        "settlement_period",
        "underlying_type",
    }
)

KNOWN_SUMMARY_FIELDS = frozenset(
    {
        *RawBookSummary.model_fields,
        "estimated_delivery_price",
        "high",
        "low",
        "mid_price",
        "price_change",
        "volume_notional",
        "volume_usd",
    }
)


def parse_rows[T: _Raw](model: type[T], rows: Iterable[Mapping[str, Any]], what: str) -> list[T]:
    parsed: list[T] = []
    for row in rows:
        try:
            parsed.append(model.model_validate(row))
        except ValidationError as error:
            name = row.get("instrument_name", "?")
            raise SchemaDriftError(f"{what} row {name!r} no longer parses: {error}") from error
    return parsed


def unexpected_fields(rows: Iterable[Mapping[str, Any]], known: frozenset[str]) -> frozenset[str]:
    seen: set[str] = set()
    for row in rows:
        seen.update(row)
    return frozenset(seen - known)
