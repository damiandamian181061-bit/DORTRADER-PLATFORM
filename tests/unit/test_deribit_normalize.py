import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from dortrader.domain import Instrument, QualityFlag, us_to_utc
from dortrader.market_data.catalog import diff_catalog
from dortrader.market_data.deribit.models import SchemaDriftError
from dortrader.market_data.normalize import normalize_summary, parse_instruments
from dortrader.market_data.quality import QualityConfig, quote_flags
from tests.conftest import load_json

CONFIG = QualityConfig()
RAW_INSTRUMENTS = load_json("deribit", "instruments_usdc_option.json")
RAW_SUMMARY = load_json("deribit", "book_summary_usdc_option.json")
CAPTURED_AT = us_to_utc(RAW_SUMMARY["usOut"])


def catalog() -> dict[str, Instrument]:
    parsed = parse_instruments(
        RAW_INSTRUMENTS["result"], underlying="BTC", settlement_currency="USDC"
    )
    return {i.name: i for i in parsed.items}


def summary(rows: list[dict[str, Any]], **kwargs: Any) -> Any:
    return normalize_summary(
        rows,
        underlying="BTC",
        received_at=kwargs.pop("received_at", CAPTURED_AT),
        raw_sha256="00" * 32,
        catalog=kwargs.pop("catalog", catalog()),
        config=CONFIG,
        **kwargs,
    )


def test_parse_instruments_keeps_only_btc_linear_usdc_options() -> None:
    parsed = parse_instruments(
        RAW_INSTRUMENTS["result"], underlying="BTC", settlement_currency="USDC"
    )

    assert len(parsed.items) == 88  # 91 captured, minus 3 SOL
    assert all(i.base_currency == "BTC" and i.settlement_currency == "USDC" for i in parsed.items)
    assert all(i.instrument_type == "linear" for i in parsed.items)
    assert parsed.unexpected_fields == frozenset()


def test_instrument_specs_come_from_fields_not_names() -> None:
    by_name = catalog()
    instrument = by_name["BTC_USDC-20OCT26-81000-P"]

    assert instrument.strike == Decimal("81000")
    assert instrument.expiration == datetime(2026, 10, 20, 8, tzinfo=UTC)
    assert instrument.contract_size == Decimal("1.0")
    assert instrument.min_trade_amount == Decimal("0.01")
    assert instrument.tick_size == Decimal("5.0")
    assert instrument.tick_for(Decimal("1500")) == Decimal("20.0")


def test_inverse_btc_options_are_not_in_the_universe() -> None:
    inverse = copy.deepcopy(RAW_INSTRUMENTS["result"][0])
    inverse.update(settlement_currency="BTC", instrument_type="reversed", quote_currency="BTC")

    parsed = parse_instruments([inverse], underlying="BTC", settlement_currency="USDC")

    assert parsed.items == []


def test_new_exchange_field_is_reported_not_fatal() -> None:
    rows = copy.deepcopy(RAW_INSTRUMENTS["result"])
    next(r for r in rows if r["base_currency"] == "BTC")["brand_new_field"] = 1

    parsed = parse_instruments(rows, underlying="BTC", settlement_currency="USDC")

    assert parsed.unexpected_fields == frozenset({"brand_new_field"})


def test_missing_required_field_is_schema_drift() -> None:
    rows = copy.deepcopy(RAW_INSTRUMENTS["result"])
    del next(r for r in rows if r["base_currency"] == "BTC")["tick_size"]

    with pytest.raises(SchemaDriftError, match="no longer parses"):
        parse_instruments(rows, underlying="BTC", settlement_currency="USDC")


def test_summary_rows_for_other_underlyings_are_excluded_and_iv_is_decimal() -> None:
    parsed = summary(RAW_SUMMARY["result"])
    raw_by_name = {r["instrument_name"]: r for r in RAW_SUMMARY["result"]}

    assert len(parsed.items) == 88
    assert parsed.unexpected_fields == frozenset()
    quote = parsed.items[0]
    assert quote.mark_iv == pytest.approx(raw_by_name[quote.instrument_name]["mark_iv"] / 100)
    assert quote.exchange_ts is not None
    assert quote.raw_sha256 == "00" * 32


def test_one_sided_quotes_are_kept_and_flagged() -> None:
    parsed = summary(RAW_SUMMARY["result"])
    by_name = {q.instrument_name: q for q in parsed.items}

    quote = by_name["BTC_USDC-20OCT26-86000-C"]
    assert QualityFlag.NO_BID in quote.flags
    assert QualityFlag.NO_ASK not in quote.flags


def test_unknown_instrument_and_duplicates_are_flagged() -> None:
    rows = copy.deepcopy(RAW_SUMMARY["result"])
    rows.append(copy.deepcopy(rows[0]))
    first = next(r for r in rows if r["base_currency"] == "BTC")
    parsed = summary(rows, catalog={})

    assert QualityFlag.UNKNOWN_INSTRUMENT in parsed.items[0].flags
    assert QualityFlag.DUPLICATE in parsed.items[-1].flags
    assert QualityFlag.DUPLICATE not in parsed.items[0].flags
    assert first["instrument_name"] == parsed.items[0].instrument_name


@pytest.mark.parametrize(
    ("bid", "ask", "expected"),
    [
        (800.0, 840.0, set()),
        (None, 840.0, {QualityFlag.NO_BID}),
        (0.0, 840.0, {QualityFlag.NO_BID}),
        (800.0, None, {QualityFlag.NO_ASK}),
        (None, None, {QualityFlag.NO_BID, QualityFlag.NO_ASK}),
        (900.0, 840.0, {QualityFlag.CROSSED}),
        (840.0, 840.0, {QualityFlag.LOCKED}),
        (100.0, 300.0, {QualityFlag.WIDE_SPREAD}),
        (801.0, 840.0, {QualityFlag.OFF_TICK}),
        (1000.0, 1020.0, set()),  # above 1000 the tick is 20
        (1000.0, 1025.0, {QualityFlag.OFF_TICK}),
    ],
)
def test_quote_flags(bid: float | None, ask: float | None, expected: set[QualityFlag]) -> None:
    instrument = catalog()["BTC_USDC-20OCT26-81000-P"]

    flags = quote_flags(
        bid=bid,
        ask=ask,
        mark_iv=0.36,
        exchange_ts=CAPTURED_AT,
        received_at=CAPTURED_AT,
        instrument=instrument,
        config=CONFIG,
    )

    assert set(flags) == expected


def test_missing_iv_and_stale_exchange_timestamp_are_flagged() -> None:
    flags = quote_flags(
        bid=800.0,
        ask=840.0,
        mark_iv=None,
        exchange_ts=CAPTURED_AT - timedelta(seconds=120),
        received_at=CAPTURED_AT,
        instrument=catalog()["BTC_USDC-20OCT26-81000-P"],
        config=CONFIG,
    )

    assert set(flags) == {QualityFlag.IV_MISSING, QualityFlag.STALE_EXCHANGE_TS}


def test_catalog_diff_reports_listing_delisting_and_state_changes() -> None:
    previous = catalog()
    names = sorted(previous)
    current = [i for i in previous.values() if i.name != names[0]]  # one delisted
    renamed = current[0].model_copy(update={"state": "halted"})  # one halted
    tweaked = current[1].model_copy(update={"tick_size": Decimal("10")})  # one attribute change
    current = [renamed, tweaked, *current[2:]]
    fresh = previous[names[0]].model_copy(update={"name": "BTC_USDC-NEW", "instrument_id": 1})
    current.append(fresh)

    diff = diff_catalog(previous, current)

    assert [i.name for i in diff.added] == ["BTC_USDC-NEW"]
    assert diff.removed == (names[0],)
    assert [(c.old, c.new) for c in diff.state_changes] == [("open", "halted")]
    assert diff.changed == (tweaked.name,)
    assert not diff.is_empty
    assert diff_catalog(previous, list(previous.values())).is_empty
