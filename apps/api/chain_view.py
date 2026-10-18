"""Turn a priced chain into the grouped shape the chain page shows. Pure; no IO."""

import statistics
from collections import defaultdict
from datetime import datetime

from apps.api.schemas import ChainResponse, ContractView, ExpiryView, StrikeView
from dortrader.domain import ChainRow, ChainSnapshot
from dortrader.pricing import ChainPricing, ContractValuation, price_chain


def nearest_expiries(snapshot: ChainSnapshot, count: int) -> ChainSnapshot:
    keep = sorted({r.instrument.expiration for r in snapshot.rows})[:count]
    rows = tuple(r for r in snapshot.rows if r.instrument.expiration in keep)
    return snapshot.model_copy(update={"rows": rows})


def contract_view(row: ChainRow, valuation: ContractValuation) -> ContractView:
    quote = row.quote
    flags = [f.value for f in (*quote.flags, *row.flags, *valuation.flags)]
    return ContractView(
        instrument_name=row.instrument.name,
        bid=quote.bid_price,
        ask=quote.ask_price,
        mark=quote.mark_price,
        quote_age_seconds=row.quote_age_seconds,
        open_interest=quote.open_interest,
        bid_iv=valuation.bid_iv,
        ask_iv=valuation.ask_iv,
        mid_iv=valuation.mid_iv,
        mark_iv=valuation.mark_iv,
        deribit_mark_iv=valuation.deribit_mark_iv,
        iv_diff_points=valuation.iv_diff_points,
        iv_spread_points=valuation.iv_spread_points,
        delta=valuation.delta,
        gamma=valuation.gamma,
        vega=valuation.vega,
        theta=valuation.theta,
        flags=list(dict.fromkeys(flags)),  # unique, in order
    )


def build_chain_response(snapshot: ChainSnapshot, expiries: int) -> ChainResponse:
    chosen = nearest_expiries(snapshot, expiries)
    pricing: ChainPricing = price_chain(chosen)
    grouped: dict[datetime, dict[float, dict[str, ContractView]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    forwards: dict[datetime, list[float]] = defaultdict(list)
    years: dict[datetime, float] = {}
    for row, valuation in zip(chosen.rows, pricing.valuations, strict=True):
        expiry = row.instrument.expiration
        grouped[expiry][float(row.instrument.strike)][row.instrument.option_type] = contract_view(
            row, valuation
        )
        years[expiry] = valuation.years_to_expiry
        if valuation.forward is not None:
            forwards[expiry].append(valuation.forward)
    views = [
        ExpiryView(
            expiration=expiry,
            forward=statistics.median(forwards[expiry]) if forwards[expiry] else None,
            years_to_expiry=years[expiry],
            strikes=[
                StrikeView(strike=strike, call=sides.get("call"), put=sides.get("put"))
                for strike, sides in sorted(grouped[expiry].items())
            ],
        )
        for expiry in sorted(grouped)
    ]
    return ChainResponse(
        snapshot_at=snapshot.snapshot_at,
        model_version=pricing.model_version,
        inputs_hash=pricing.inputs_hash,
        listed_without_quote=len(snapshot.listed_without_quote),
        quotes_without_instrument=len(snapshot.quotes_without_instrument),
        expiries=views,
    )
