"""Pure functions from raw Deribit rows to domain entities. No IO."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dortrader.domain import (
    Bar,
    Instrument,
    OptionQuote,
    QualityFlag,
    TickStep,
    ms_to_utc,
    vol_from_percent,
)
from dortrader.market_data.deribit.models import (
    KNOWN_CHART_FIELDS,
    KNOWN_INSTRUMENT_FIELDS,
    KNOWN_SUMMARY_FIELDS,
    KNOWN_VOL_INDEX_FIELDS,
    RawBookSummary,
    RawChart,
    RawInstrument,
    RawVolIndex,
    SchemaDriftError,
    parse_rows,
    unexpected_fields,
)
from dortrader.market_data.quality import QualityConfig, quote_flags


@dataclass(frozen=True)
class Parsed[T]:
    items: list[T]
    unexpected_fields: frozenset[str]


def parse_instruments(
    rows: Sequence[Mapping[str, Any]], *, underlying: str, settlement_currency: str
) -> Parsed[Instrument]:
    """Linear options on `underlying` settled in `settlement_currency`; everything else is skipped.

    Inverse (coin-settled) options share the underlying but are a different product.
    """
    selected = [
        row
        for row in rows
        if row.get("kind") == "option"
        and row.get("base_currency") == underlying
        and row.get("settlement_currency") == settlement_currency
        and row.get("instrument_type") == "linear"
    ]
    raw = parse_rows(RawInstrument, selected, "instrument")
    instruments = [
        Instrument(
            instrument_id=r.instrument_id,
            name=r.instrument_name,
            base_currency=r.base_currency,
            quote_currency=r.quote_currency,
            settlement_currency=r.settlement_currency,
            instrument_type=r.instrument_type,
            option_type=r.option_type,
            strike=r.strike,
            expiration=ms_to_utc(r.expiration_timestamp),
            created_at=ms_to_utc(r.creation_timestamp),
            contract_size=r.contract_size,
            tick_size=r.tick_size,
            tick_size_steps=tuple(
                TickStep(above_price=s.above_price, tick_size=s.tick_size)
                for s in r.tick_size_steps
            ),
            min_trade_amount=r.min_trade_amount,
            state=r.state,
            is_active=r.is_active,
            price_index=r.price_index,
            maker_commission=r.maker_commission,
            taker_commission=r.taker_commission,
        )
        for r in raw
    ]
    return Parsed(instruments, unexpected_fields(selected, KNOWN_INSTRUMENT_FIELDS))


def normalize_summary(
    rows: Sequence[Mapping[str, Any]],
    *,
    underlying: str,
    received_at: datetime,
    raw_sha256: str,
    catalog: Mapping[str, Instrument],
    config: QualityConfig,
    source: str = "deribit",
    channel: str = "book_summary",
) -> Parsed[OptionQuote]:
    """One OptionQuote per row for `underlying`. Flags describe quality; no row is dropped."""
    selected = [row for row in rows if row.get("base_currency") == underlying]
    raw = parse_rows(RawBookSummary, selected, "book summary")
    quotes: list[OptionQuote] = []
    seen: set[str] = set()
    for r in raw:
        exchange_ts = ms_to_utc(r.creation_timestamp) if r.creation_timestamp is not None else None
        iv = vol_from_percent(r.mark_iv) if r.mark_iv is not None else None
        flags = set(
            quote_flags(
                bid=r.bid_price,
                ask=r.ask_price,
                mark_iv=iv,
                exchange_ts=exchange_ts,
                received_at=received_at,
                instrument=catalog.get(r.instrument_name),
                config=config,
            )
        )
        if r.instrument_name in seen:
            flags.add(QualityFlag.DUPLICATE)
        seen.add(r.instrument_name)
        quotes.append(
            OptionQuote(
                instrument_name=r.instrument_name,
                source=source,
                channel=channel,
                received_at=received_at,
                exchange_ts=exchange_ts,
                bid_price=r.bid_price,
                ask_price=r.ask_price,
                mark_price=r.mark_price,
                mark_iv=iv,
                underlying_price=r.underlying_price,
                underlying_index=r.underlying_index,
                interest_rate=r.interest_rate,
                open_interest=r.open_interest,
                volume=r.volume,
                last_price=r.last,
                flags=tuple(sorted(flags, key=list(QualityFlag).index)),
                raw_sha256=raw_sha256,
            )
        )
    return Parsed(quotes, unexpected_fields(selected, KNOWN_SUMMARY_FIELDS))


def parse_chart(result: Mapping[str, Any], *, instrument: str, step_seconds: int) -> Parsed[Bar]:
    """Candles from get_tradingview_chart_data. status "no_data" is an empty, valid answer."""
    raw = parse_rows(RawChart, [result], "chart data")[0]
    if raw.status == "no_data":
        return Parsed([], unexpected_fields([result], KNOWN_CHART_FIELDS))
    lengths = {len(a) for a in (raw.ticks, raw.open, raw.high, raw.low, raw.close, raw.volume)}
    if len(lengths) != 1:
        raise SchemaDriftError(f"chart data arrays have different lengths: {sorted(lengths)}")
    bars = [
        Bar(
            instrument=instrument,
            step_seconds=step_seconds,
            start=ms_to_utc(ts),
            open=o,
            high=h,
            low=lo,
            close=c,
            volume=v,
        )
        for ts, o, h, lo, c, v in zip(
            raw.ticks, raw.open, raw.high, raw.low, raw.close, raw.volume, strict=True
        )
    ]
    return Parsed(bars, unexpected_fields([result], KNOWN_CHART_FIELDS))


def parse_vol_index(
    result: Mapping[str, Any], *, name: str, step_seconds: int
) -> tuple[Parsed[Bar], int | None]:
    """DVOL candles plus the continuation value to page backwards (None when finished)."""
    raw = parse_rows(RawVolIndex, [result], "volatility index")[0]
    bars = []
    for row in raw.data:
        if len(row) != 5:
            raise SchemaDriftError(f"volatility index row has {len(row)} values, expected 5")
        ts, o, h, lo, c = row
        bars.append(
            Bar(
                instrument=name,
                step_seconds=step_seconds,
                start=ms_to_utc(int(ts)),
                open=o,
                high=h,
                low=lo,
                close=c,
            )
        )
    parsed = Parsed(bars, unexpected_fields([result], KNOWN_VOL_INDEX_FIELDS))
    return parsed, raw.continuation
