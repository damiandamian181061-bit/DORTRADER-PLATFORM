"""Quality flags for a quote. Flags describe the data; they never remove it."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from dortrader.domain import Instrument, QualityFlag


@dataclass(frozen=True)
class QualityConfig:
    wide_spread_ratio: float = 0.5  # (ask - bid) / mid above this is flagged
    stale_exchange_seconds: float = 60.0


def _off_tick(price: float, instrument: Instrument) -> bool:
    value = Decimal(str(price))
    return value % instrument.tick_for(value) != 0


def quote_flags(
    *,
    bid: float | None,
    ask: float | None,
    mark_iv: float | None,
    exchange_ts: datetime | None,
    received_at: datetime,
    instrument: Instrument | None,
    config: QualityConfig,
) -> tuple[QualityFlag, ...]:
    flags: set[QualityFlag] = set()
    has_bid = bid is not None and bid > 0
    has_ask = ask is not None and ask > 0
    if not has_bid:
        flags.add(QualityFlag.NO_BID)
    if not has_ask:
        flags.add(QualityFlag.NO_ASK)
    if bid is not None and ask is not None and has_bid and has_ask:
        if bid > ask:
            flags.add(QualityFlag.CROSSED)
        elif bid == ask:
            flags.add(QualityFlag.LOCKED)
        elif (ask - bid) / ((ask + bid) / 2) > config.wide_spread_ratio:
            flags.add(QualityFlag.WIDE_SPREAD)
    if mark_iv is None or mark_iv <= 0:
        flags.add(QualityFlag.IV_MISSING)
    if exchange_ts is not None:
        if (received_at - exchange_ts).total_seconds() > config.stale_exchange_seconds:
            flags.add(QualityFlag.STALE_EXCHANGE_TS)
    if instrument is None:
        flags.add(QualityFlag.UNKNOWN_INSTRUMENT)
    else:
        if any(p is not None and p > 0 and _off_tick(p, instrument) for p in (bid, ask)):
            flags.add(QualityFlag.OFF_TICK)
    return tuple(sorted(flags, key=list(QualityFlag).index))
