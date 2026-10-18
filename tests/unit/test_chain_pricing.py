import math
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from dortrader.domain import ChainRow, ChainSnapshot, Instrument, OptionQuote, QualityFlag
from dortrader.pricing import PRICING_MODEL_VERSION, price, price_chain, time_to_expiry_years
from dortrader.replay import ChainStore
from tests.helpers import Rig

AT = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
EXPIRY = AT + timedelta(days=2)
FORWARD = 81_000.0


def smile(strike: float) -> float:
    k = math.log(strike / FORWARD)
    return 0.40 + 0.60 * k * k


def tick_for(premium: float) -> float:
    return 20.0 if premium > 1000 else 5.0


def make_row(
    strike: float, is_call: bool, *, shift: float = 0.0, with_bid: bool = True
) -> ChainRow:
    years = time_to_expiry_years(AT, EXPIRY)
    sigma = smile(strike)
    mid = float(price(FORWARD, strike, years, sigma, is_call))
    tick = tick_for(mid)
    bid = max(math.floor((mid - tick) / tick) * tick, 0.0) + shift
    ask = math.ceil((mid + tick) / tick) * tick + shift
    side = "C" if is_call else "P"
    instrument = Instrument(
        instrument_id=int(strike) + (1 if is_call else 0),
        name=f"BTC_USDC-20OCT26-{int(strike)}-{side}",
        base_currency="BTC",
        quote_currency="USDC",
        settlement_currency="USDC",
        instrument_type="linear",
        option_type="call" if is_call else "put",
        strike=Decimal(str(strike)),
        expiration=EXPIRY,
        created_at=AT - timedelta(days=3),
        contract_size=Decimal("1"),
        tick_size=Decimal("5"),
        tick_size_steps=[{"above_price": Decimal("1000"), "tick_size": Decimal("20")}],
        min_trade_amount=Decimal("0.01"),
        state="open",
        is_active=True,
        price_index="btc_usdc",
    )
    quote = OptionQuote(
        instrument_name=instrument.name,
        source="deribit",
        channel="book_summary",
        received_at=AT,
        exchange_ts=AT,
        bid_price=bid if with_bid and bid > 0 else None,
        ask_price=ask,
        mark_price=mid + shift,
        mark_iv=sigma,
        underlying_price=FORWARD,
        underlying_index="BTC_USDC-20OCT26",
        interest_rate=0.0,
        open_interest=1.0,
        volume=0.0,
        last_price=None,
        raw_sha256="00" * 32,
    )
    return ChainRow(instrument=instrument, quote=quote, quote_age_seconds=0.0)


def fair(strike: float, is_call: bool) -> float:
    return float(price(FORWARD, strike, time_to_expiry_years(AT, EXPIRY), smile(strike), is_call))


def snapshot_of(rows: list[ChainRow]) -> ChainSnapshot:
    ordered = sorted(rows, key=lambda r: (r.instrument.strike, r.instrument.option_type))
    return ChainSnapshot(snapshot_at=AT, rows=tuple(ordered))


def clean_rows(strikes: range = range(70_000, 92_001, 1000)) -> list[ChainRow]:
    return [make_row(float(k), is_call) for k in strikes for is_call in (True, False)]


def flags_by_name(rows: list[ChainRow]) -> dict[str, set[QualityFlag]]:
    pricing = price_chain(snapshot_of(rows))
    return {v.instrument_name: set(v.flags) for v in pricing.valuations if v.flags}


def test_a_clean_chain_raises_no_flags_and_recovers_the_generating_vols() -> None:
    pricing = price_chain(snapshot_of(clean_rows()))

    assert not any(v.flags for v in pricing.valuations)  # rounding to ticks is not arbitrage
    for v in pricing.valuations:
        assert v.mark_iv_status == "OK"
        assert v.mark_iv == pytest.approx(smile(v.strike), abs=1e-7)
        assert v.iv_diff_points == pytest.approx(0.0, abs=1e-5)
        assert v.delta is not None and v.vega is not None and v.theta is not None
        assert v.log_moneyness == pytest.approx(math.log(v.strike / FORWARD))
    assert pricing.model_version == PRICING_MODEL_VERSION


def test_bid_mid_ask_vols_are_ordered_and_the_spread_is_reported() -> None:
    for v in price_chain(snapshot_of(clean_rows())).valuations:
        if v.bid_iv is not None and v.ask_iv is not None and v.mid_iv is not None:
            assert v.bid_iv <= v.mid_iv <= v.ask_iv
            assert v.iv_spread_points == pytest.approx((v.ask_iv - v.bid_iv) * 100)


def test_a_missing_bid_gives_no_bid_vol_but_everything_else_still_prices() -> None:
    rows = [make_row(85_000.0, True, with_bid=False), make_row(85_000.0, False)]

    call, put = price_chain(snapshot_of(rows)).valuations

    assert call.bid_iv is None and call.mid_iv is None
    assert call.ask_iv is not None and call.mark_iv is not None
    assert put.bid_iv is not None


def bump_call_80000(shift: float) -> list[ChainRow]:
    return [
        make_row(80_000.0, True, shift=shift) if r.instrument.name.endswith("80000-C") else r
        for r in clean_rows()
    ]


def test_a_moderately_overpriced_call_breaks_convexity_and_parity() -> None:
    # 300 is less than the gap to the next strike down, so prices still fall with strike; the
    # call is simply too dear against its neighbours and against its put.
    assert flags_by_name(bump_call_80000(300.0)) == {
        "BTC_USDC-20OCT26-80000-C": {QualityFlag.BUTTERFLY_VIOLATION, QualityFlag.PARITY_VIOLATION},
        "BTC_USDC-20OCT26-80000-P": {QualityFlag.PARITY_VIOLATION},
    }


def test_a_call_dearer_than_the_next_lower_strike_breaks_monotonicity_too() -> None:
    gap = fair(79_000.0, True) - fair(80_000.0, True)  # how much cheaper 80000 should be
    assert flags_by_name(bump_call_80000(gap + 200.0)) == {
        "BTC_USDC-20OCT26-79000-C": {QualityFlag.MONOTONICITY_VIOLATION},
        "BTC_USDC-20OCT26-80000-C": {
            QualityFlag.MONOTONICITY_VIOLATION,
            QualityFlag.BUTTERFLY_VIOLATION,
            QualityFlag.PARITY_VIOLATION,
        },
        "BTC_USDC-20OCT26-80000-P": {QualityFlag.PARITY_VIOLATION},
    }


@pytest.mark.parametrize("shift", [120.0, -120.0])
def test_parity_is_checked_in_both_directions_at_a_single_strike(shift: float) -> None:
    rows = [make_row(81_000.0, True), make_row(81_000.0, False, shift=shift)]

    assert flags_by_name(rows) == {
        "BTC_USDC-20OCT26-81000-C": {QualityFlag.PARITY_VIOLATION},
        "BTC_USDC-20OCT26-81000-P": {QualityFlag.PARITY_VIOLATION},
    }


def test_a_mispriced_put_is_caught_by_put_monotonicity() -> None:
    gap = fair(84_000.0, False) - fair(83_000.0, False)  # puts should cost more at 84000
    bumped = [
        make_row(84_000.0, False, shift=-(gap + 200.0))
        if r.instrument.name.endswith("84000-P")
        else r
        for r in clean_rows()
    ]

    flags = flags_by_name(bumped)

    assert QualityFlag.MONOTONICITY_VIOLATION in flags["BTC_USDC-20OCT26-83000-P"]
    assert QualityFlag.MONOTONICITY_VIOLATION in flags["BTC_USDC-20OCT26-84000-P"]


def test_pricing_is_deterministic_and_the_hash_follows_the_inputs() -> None:
    snap = snapshot_of(clean_rows())
    first, second = price_chain(snap), price_chain(snap)
    moved = snapshot_of([make_row(80_000.0, True, shift=1.0), *clean_rows()[2:]])

    assert first == second and first.model_dump_json() == second.model_dump_json()
    assert price_chain(moved).inputs_hash != first.inputs_hash


@pytest.mark.anyio
async def test_a_recorded_chain_prices_cleanly_and_matches_deribits_marks(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    await rig.recorder.run_cycle()
    rig.recorder.close()
    chain = ChainStore(tmp_path).as_of(rig.clock.now())

    pricing = price_chain(chain)

    assert len(pricing.valuations) == len(chain.rows) == 88
    assert Counter(v.mark_iv_status for v in pricing.valuations) == {"OK": 88}
    assert not any(v.flags for v in pricing.valuations)  # real data raises no false alarms
    assert pricing == price_chain(chain)
    otm = [
        v
        for v in pricing.valuations
        if v.forward is not None
        and (
            (v.option_type == "call" and v.strike > v.forward)
            or (v.option_type == "put" and v.strike < v.forward)
        )
    ]
    assert len(otm) > 30
    # Out of the money, our vol from Deribit's mark price reproduces Deribit's mark_iv to within
    # a few times its 0.01-point rounding. In the money the price is mostly intrinsic value and
    # the inversion is ill-conditioned, so no claim is made there.
    assert max(abs(v.iv_diff_points or 0.0) for v in otm) < 0.05
