import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from dortrader.domain import (
    ChainSnapshot,
    Instrument,
    Money,
    OptionQuote,
    QualityFlag,
    ms_to_utc,
    vol_from_percent,
)

SCHEMA_DIR = Path(__file__).parent.parent / "fixtures" / "schemas"
T0 = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def make_instrument(**overrides: Any) -> Instrument:
    fields: dict[str, Any] = {
        "instrument_id": 715465,
        "name": "BTC_USDC-19OCT26-71000-C",
        "base_currency": "BTC",
        "quote_currency": "USDC",
        "settlement_currency": "USDC",
        "instrument_type": "linear",
        "option_type": "call",
        "strike": Decimal("71000"),
        "expiration": datetime(2026, 10, 19, 8, tzinfo=UTC),
        "created_at": datetime(2026, 10, 16, 8, tzinfo=UTC),
        "contract_size": Decimal("1.0"),
        "tick_size": Decimal("5.0"),
        "tick_size_steps": [{"above_price": Decimal("1000"), "tick_size": Decimal("20")}],
        "min_trade_amount": Decimal("0.01"),
        "state": "open",
        "is_active": True,
        "price_index": "btc_usdc",
    }
    fields.update(overrides)
    return Instrument(**fields)


def make_quote(**overrides: Any) -> OptionQuote:
    fields: dict[str, Any] = {
        "instrument_name": "BTC_USDC-19OCT26-71000-C",
        "source": "deribit",
        "channel": "book_summary",
        "received_at": T0,
        "exchange_ts": T0,
        "bid_price": 800.0,
        "ask_price": 840.0,
        "mark_price": 824.8,
        "mark_iv": 0.365,
        "underlying_price": 81117.5,
        "underlying_index": "BTC_USDC-23OCT26",
        "interest_rate": 0.0,
        "open_interest": 37.71,
        "volume": 0.0,
        "last_price": 575.0,
        "raw_sha256": "ab" * 32,
    }
    fields.update(overrides)
    return OptionQuote(**fields)


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        make_quote(received_at=datetime(2026, 10, 18, 12, 0))


def test_non_utc_datetime_is_converted_to_utc() -> None:
    from datetime import timedelta, timezone

    plus_two = timezone(timedelta(hours=2))
    quote = make_quote(received_at=datetime(2026, 10, 18, 14, 0, tzinfo=plus_two))

    assert quote.received_at == T0
    assert quote.received_at.utcoffset() == timedelta(0)


def test_crossed_quote_is_accepted_not_rejected() -> None:
    quote = make_quote(bid_price=900.0, ask_price=840.0)

    assert quote.bid_price == 900.0


def test_negative_open_interest_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_quote(open_interest=-1.0)


def test_nan_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_quote(mark_price=float("nan"))


def test_unknown_currency_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_instrument(quote_currency="DOGE")
    with pytest.raises(ValidationError):
        Money(amount=Decimal("1"), currency="DOGE")


def test_instrument_rejects_expiry_before_creation() -> None:
    with pytest.raises(ValidationError, match="expiration"):
        make_instrument(expiration=datetime(2026, 10, 15, tzinfo=UTC))


def test_instrument_rejects_zero_strike_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        make_instrument(strike=Decimal("0"))
    with pytest.raises(ValidationError):
        make_instrument(surprise=1)


def test_tick_steps_apply_above_threshold() -> None:
    instrument = make_instrument()

    assert instrument.tick_for(Decimal("1000")) == Decimal("5.0")
    assert instrument.tick_for(Decimal("1000.5")) == Decimal("20")
    assert instrument.tick_for(Decimal("15")) == Decimal("5.0")


def test_entities_are_frozen() -> None:
    quote = make_quote()
    with pytest.raises(ValidationError):
        quote.mark_price = 1.0  # type: ignore[misc]


def test_conversions() -> None:
    assert vol_from_percent(36.5) == 0.365
    assert ms_to_utc(1792396800000) == datetime(2026, 10, 19, 8, tzinfo=UTC)
    assert QualityFlag("crossed") is QualityFlag.CROSSED


finite = st.floats(min_value=0, max_value=1e9, allow_nan=False, allow_infinity=False)


@given(
    bid=st.none() | finite,
    ask=st.none() | finite,
    mark=finite,
    iv=st.none() | st.floats(0, 20, allow_nan=False),
    received=st.datetimes(timezones=st.just(UTC)),
)
def test_quote_json_round_trip(
    bid: float | None, ask: float | None, mark: float, iv: float | None, received: datetime
) -> None:
    quote = make_quote(
        bid_price=bid, ask_price=ask, mark_price=mark, mark_iv=iv, received_at=received
    )

    assert OptionQuote.model_validate_json(quote.model_dump_json()) == quote


@pytest.mark.parametrize(
    ("model", "filename"),
    [
        (Instrument, "instrument.schema.json"),
        (OptionQuote, "option_quote.schema.json"),
        (ChainSnapshot, "chain_snapshot.schema.json"),
    ],
)
def test_json_schema_snapshot(
    model: type[Instrument | OptionQuote | ChainSnapshot], filename: str
) -> None:
    """Changing a domain contract must be deliberate: rerun with UPDATE_SNAPSHOTS=1."""
    path = SCHEMA_DIR / filename
    current = json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_SNAPSHOTS"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(current, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == current
