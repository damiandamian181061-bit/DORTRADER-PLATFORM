"""Static no-arbitrage checks on one chain snapshot, using executable bid/ask prices.

Each rule only fires when buying at the ask and selling at the bid would still leave a profit
bigger than a tick, so quoting noise and the spread itself never raise a flag. Fees, margin and
size are ignored: a flag means "look at this quote", not "trade it". Cross-expiry (calendar)
checks need a volatility surface and are not done here.

Rules, per expiry, over adjacent listed strikes K1 < K2 < K3:
  monotonicity  calls cannot get dearer as strike rises (puts cannot get cheaper)
  butterfly     option prices are convex in strike: price(K2) <= w*price(K1) + (1-w)*price(K3)
  parity        call - put = df * (F - K) at a shared strike
"""

import math
from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal

from dortrader.domain import ChainRow, QualityFlag


def _tick(row: ChainRow, price: float) -> float:
    return float(row.instrument.tick_for(Decimal(str(price))))


def _bid(row: ChainRow) -> float | None:
    bid = row.quote.bid_price
    return bid if bid is not None and bid > 0 else None


def _ask(row: ChainRow) -> float | None:
    ask = row.quote.ask_price
    return ask if ask is not None and ask > 0 else None


def find_violations(rows: Sequence[ChainRow]) -> dict[str, set[QualityFlag]]:
    """Map instrument name to the flags it earned. Rows are grouped by expiry internally."""
    flags: dict[str, set[QualityFlag]] = defaultdict(set)
    by_expiry: dict[object, dict[str, list[ChainRow]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_expiry[row.instrument.expiration][row.instrument.option_type].append(row)

    for sides in by_expiry.values():
        for option_type in ("call", "put"):
            line = sorted(sides.get(option_type, []), key=lambda r: r.instrument.strike)
            _monotonicity(line, option_type == "call", flags)
            _butterfly(line, flags)
        _parity(sides.get("call", []), sides.get("put", []), flags)
    return dict(flags)


def _monotonicity(
    line: Sequence[ChainRow], is_call: bool, flags: dict[str, set[QualityFlag]]
) -> None:
    for low, high in zip(line, line[1:], strict=False):
        # call: K_high must not cost more than K_low; put: K_low must not cost more than K_high.
        cheaper, dearer = (high, low) if is_call else (low, high)
        sell, buy = _bid(cheaper), _ask(dearer)
        # Selling `cheaper` at its bid while buying `dearer` at its ask would be a free credit.
        if sell is not None and buy is not None and sell - buy > _tick(cheaper, sell):
            flags[low.instrument.name].add(QualityFlag.MONOTONICITY_VIOLATION)
            flags[high.instrument.name].add(QualityFlag.MONOTONICITY_VIOLATION)


def _butterfly(line: Sequence[ChainRow], flags: dict[str, set[QualityFlag]]) -> None:
    for a, b, c in zip(line, line[1:], line[2:], strict=False):
        k1, k2, k3 = (float(r.instrument.strike) for r in (a, b, c))
        weight = (k3 - k2) / (k3 - k1)
        sell, buy_a, buy_c = _bid(b), _ask(a), _ask(c)
        if sell is None or buy_a is None or buy_c is None:
            continue
        if sell - (weight * buy_a + (1 - weight) * buy_c) > _tick(b, sell):
            flags[b.instrument.name].add(QualityFlag.BUTTERFLY_VIOLATION)


def _parity(
    calls: Sequence[ChainRow], puts: Sequence[ChainRow], flags: dict[str, set[QualityFlag]]
) -> None:
    puts_by_strike = {r.instrument.strike: r for r in puts}
    for call in calls:
        put = puts_by_strike.get(call.instrument.strike)
        forward = call.quote.underlying_price
        if put is None or forward is None:
            continue
        rate = call.quote.interest_rate or 0.0
        years = max(0.0, (call.instrument.expiration - call.quote.received_at).total_seconds())
        df = math.exp(-rate * years / (365.0 * 86_400.0))
        parity = df * (forward - float(call.instrument.strike))
        call_bid, call_ask, put_bid, put_ask = _bid(call), _ask(call), _bid(put), _ask(put)
        tolerance = _tick(call, call_ask or call_bid or 1.0) + _tick(put, put_ask or put_bid or 1.0)
        violated = False
        if call_ask is not None and put_bid is not None:  # buy call, sell put: cost vs parity
            violated |= (call_ask - put_bid) < parity - tolerance
        if call_bid is not None and put_ask is not None:  # sell call, buy put: credit vs parity
            violated |= (call_bid - put_ask) > parity + tolerance
        if violated:
            flags[call.instrument.name].add(QualityFlag.PARITY_VIOLATION)
            flags[put.instrument.name].add(QualityFlag.PARITY_VIOLATION)
