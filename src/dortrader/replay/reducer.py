"""Pure aggregation of quotes into a chain snapshot. Live recording and replay share this code,
so a replayed chain is the chain the live aggregator saw."""

from collections.abc import Iterable, Mapping
from datetime import datetime

from dortrader.domain import (
    ChainRow,
    ChainSnapshot,
    Instrument,
    OptionQuote,
    QualityFlag,
)

State = Mapping[str, OptionQuote]


def apply(state: State, quote: OptionQuote) -> dict[str, OptionQuote]:
    """New state with `quote` applied: the latest receipt per instrument wins, ties go to the
    later input. An older, out-of-order quote never replaces a newer one."""
    return fold(state, [quote])


def fold(state: State, quotes: Iterable[OptionQuote]) -> dict[str, OptionQuote]:
    new = dict(state)
    for quote in quotes:
        current = new.get(quote.instrument_name)
        if current is None or quote.received_at >= current.received_at:
            new[quote.instrument_name] = quote
    return new


def snapshot(
    state: State,
    instruments: Iterable[Instrument],
    at: datetime,
    *,
    stale_after_seconds: float,
) -> ChainSnapshot:
    """Join the latest quotes with the instruments listed at `at` (created, not yet expired)."""
    listed = {i.name: i for i in instruments if i.created_at <= at < i.expiration}
    rows: list[ChainRow] = []
    for name, instrument in listed.items():
        quote = state.get(name)
        if quote is None:
            continue
        age = (at - quote.received_at).total_seconds()
        flags = (QualityFlag.STALE_NO_UPDATE,) if age > stale_after_seconds else ()
        rows.append(
            ChainRow(instrument=instrument, quote=quote, quote_age_seconds=age, flags=flags)
        )
    rows.sort(
        key=lambda r: (r.instrument.expiration, r.instrument.strike, r.instrument.option_type)
    )
    return ChainSnapshot(
        snapshot_at=at,
        rows=tuple(rows),
        listed_without_quote=tuple(sorted(n for n in listed if n not in state)),
        quotes_without_instrument=tuple(sorted(n for n in state if n not in listed)),
    )
