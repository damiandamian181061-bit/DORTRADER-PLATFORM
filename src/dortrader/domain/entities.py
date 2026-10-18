"""Typed entities for the instrument catalog and option quotes.

Specs (strike, tick, contract size) are Decimal so tick-grid checks are exact.
Quote prices are float64 observations; they become Money only in ledger contexts.
Structural problems raise. Market-quality problems become QualityFlag values: the
exchange is untrusted, but its data is recorded, never silently dropped.
"""

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from dortrader.domain.types import Currency, FrozenModel, UtcDateTime

PositiveDecimal = Annotated[Decimal, Field(gt=0)]
NonNegativeFloat = Annotated[float, Field(ge=0)]

InstrumentState = Literal[
    "open", "settlement", "delivered", "inactive", "locked", "halted", "archivized"
]


class QualityFlag(StrEnum):
    NO_BID = "no_bid"
    NO_ASK = "no_ask"
    CROSSED = "crossed"
    LOCKED = "locked"
    WIDE_SPREAD = "wide_spread"
    OFF_TICK = "off_tick"
    IV_MISSING = "iv_missing"
    STALE_EXCHANGE_TS = "stale_exchange_ts"
    DUPLICATE = "duplicate"
    UNKNOWN_INSTRUMENT = "unknown_instrument"
    # Reserved for the WebSocket tier and later workstreams.
    STALE_NO_UPDATE = "stale_no_update"
    OUTSIDE_PRICE_BAND = "outside_price_band"
    UNDERLYING_STALE = "underlying_stale"
    SEQUENCE_GAP = "sequence_gap"
    CLOCK_DRIFT = "clock_drift"


class TickStep(FrozenModel):
    """Prices above `above_price` move in steps of `tick_size`."""

    above_price: Decimal
    tick_size: PositiveDecimal


class Instrument(FrozenModel):
    instrument_id: int
    name: str
    base_currency: Currency
    quote_currency: Currency
    settlement_currency: Currency
    instrument_type: Literal["linear", "reversed"]
    option_type: Literal["call", "put"]
    strike: PositiveDecimal
    expiration: UtcDateTime
    created_at: UtcDateTime
    contract_size: PositiveDecimal
    tick_size: PositiveDecimal
    tick_size_steps: tuple[TickStep, ...] = ()
    min_trade_amount: PositiveDecimal
    state: InstrumentState
    is_active: bool
    price_index: str
    maker_commission: Decimal | None = None
    taker_commission: Decimal | None = None

    @model_validator(mode="after")
    def _expiry_after_creation(self) -> "Instrument":
        if self.expiration <= self.created_at:
            raise ValueError("expiration must be after creation")
        return self

    def tick_for(self, price: Decimal) -> Decimal:
        tick = self.tick_size
        threshold: Decimal | None = None
        for step in self.tick_size_steps:
            if price > step.above_price and (threshold is None or step.above_price > threshold):
                tick, threshold = step.tick_size, step.above_price
        return tick


class OptionQuote(FrozenModel):
    """One observation of an option's top of book and mark, as received."""

    instrument_name: str
    source: str
    channel: str
    received_at: UtcDateTime
    exchange_ts: UtcDateTime | None
    bid_price: NonNegativeFloat | None
    ask_price: NonNegativeFloat | None
    mark_price: NonNegativeFloat
    mark_iv: NonNegativeFloat | None  # decimal fraction, not percent
    underlying_price: Annotated[float, Field(gt=0)] | None
    underlying_index: str | None
    interest_rate: float | None
    open_interest: NonNegativeFloat
    volume: NonNegativeFloat | None
    last_price: NonNegativeFloat | None
    flags: tuple[QualityFlag, ...] = ()
    raw_sha256: str
    connection_epoch: int = 0
