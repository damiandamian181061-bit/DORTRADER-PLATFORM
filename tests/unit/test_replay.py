import copy
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from dortrader.domain import OptionQuote, QualityFlag
from dortrader.replay import ChainStore, NoRecordedData, apply, fold, snapshot
from tests.helpers import START, FakeClient, Rig

pytestmark = pytest.mark.anyio


def quote(name: str, received: datetime, mark: float = 100.0) -> OptionQuote:
    return OptionQuote(
        instrument_name=name,
        source="deribit",
        channel="book_summary",
        received_at=received,
        exchange_ts=received,
        bid_price=mark - 1,
        ask_price=mark + 1,
        mark_price=mark,
        mark_iv=0.4,
        underlying_price=81_000.0,
        underlying_index="BTC_USDC-20OCT26",
        interest_rate=0.0,
        open_interest=1.0,
        volume=0.0,
        last_price=None,
        raw_sha256="00" * 32,
    )


T0 = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def test_the_latest_receipt_wins_and_old_quotes_never_replace_new_ones() -> None:
    first, second = quote("A", T0, 1.0), quote("A", T0 + timedelta(seconds=30), 2.0)
    older = quote("A", T0 - timedelta(seconds=30), 3.0)
    state = apply({}, first)

    assert apply(state, second)["A"] == second
    assert apply(apply(state, second), older)["A"] == second  # out of order: ignored
    assert state == {"A": first}  # apply is pure


def test_a_tie_on_receipt_time_goes_to_the_later_input() -> None:
    a, b = quote("A", T0, 1.0), quote("A", T0, 2.0)

    assert fold({}, [a, b])["A"] == b


def test_fold_equals_repeated_apply() -> None:
    quotes = [quote(n, T0 + timedelta(seconds=s), 100.0 + s) for s in (0, 30, 60) for n in "ABC"]
    state: dict[str, OptionQuote] = {}
    for q in quotes:
        state = apply(state, q)

    assert state == fold({}, quotes)


# --- end to end: recorder -> archive -> ChainStore -------------------------------------------


def add_instrument(client: FakeClient, name: str, *, created: datetime, expires: datetime) -> None:
    template = next(i for i in client.instruments if i["base_currency"] == "BTC")
    row = next(r for r in client.summary if r["base_currency"] == "BTC")
    instrument = copy.deepcopy(template) | {
        "instrument_name": name,
        "instrument_id": 999_001,
        "creation_timestamp": int(created.timestamp() * 1000),
        "expiration_timestamp": int(expires.timestamp() * 1000),
    }
    client.instruments.append(instrument)
    client.summary.append(copy.deepcopy(row) | {"instrument_name": name})


async def record(
    rig: Rig, cycles: int, step: float = 30, mutate: Callable[[int, FakeClient], None] | None = None
) -> list[tuple[datetime, Any]]:
    """Run cycles, returning (time, snapshot built from the recorder's own live reducer state)."""
    live = []
    for i in range(cycles):
        if mutate:
            mutate(i, rig.client)
        await rig.recorder.run_cycle()
        now = rig.clock.now()
        live.append(
            (
                now,
                snapshot(
                    rig.recorder.chain_state,
                    rig.recorder.catalog.values(),
                    now,
                    stale_after_seconds=90.0,
                ),
            )
        )
        rig.clock.advance(step)
    rig.recorder.close()
    return live


async def test_replay_reproduces_what_the_live_aggregator_saw(tmp_path: Path) -> None:
    rig = Rig(tmp_path, catalog_refresh_seconds=50)

    def mutate(cycle: int, client: FakeClient) -> None:
        for row in client.summary:
            row["mark_price"] = round(row["mark_price"] + cycle, 4)
        if cycle == 1:  # a contract listed mid-run
            add_instrument(
                client,
                "BTC_USDC-20OCT26-99999-C",
                created=START + timedelta(seconds=45),
                expires=START + timedelta(days=2),
            )
        if cycle == 2:  # and one that stops quoting
            btc = [r for r in client.summary if r["base_currency"] == "BTC"]
            client.summary.remove(btc[0])

    live = await record(rig, 4, mutate=mutate)

    store = ChainStore(tmp_path)
    for at, expected in live:
        assert store.as_of(at) == expected
    assert live[0][1].rows != live[3][1].rows  # the cycles genuinely differed


async def test_the_same_request_gives_identical_output(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    live = await record(rig, 2)
    at = live[1][0]

    first, second = ChainStore(tmp_path).as_of(at), ChainStore(tmp_path).as_of(at)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()


async def test_between_cycles_you_see_the_earlier_quotes_with_their_age(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    def bump(cycle: int, client: FakeClient) -> None:
        for row in client.summary:
            row["mark_price"] = 1000.0 + cycle

    live = await record(rig, 2, mutate=bump)
    between = live[0][0] + timedelta(seconds=12)

    chain = ChainStore(tmp_path).as_of(between)

    assert {r.quote.mark_price for r in chain.rows} == {1000.0}  # cycle 0, not the later cycle 1
    assert {round(r.quote_age_seconds) for r in chain.rows} == {12}
    assert all(QualityFlag.STALE_NO_UPDATE not in r.flags for r in chain.rows)


async def test_a_recording_gap_yields_stale_flags_not_silently_old_data(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    live = await record(rig, 1, step=300)
    await rig.recorder.run_cycle()
    rig.recorder.close()
    just_before = live[0][0] + timedelta(seconds=299)

    chain = ChainStore(tmp_path).as_of(just_before)

    assert chain.rows
    assert all(r.flags == (QualityFlag.STALE_NO_UPDATE,) for r in chain.rows)
    assert min(r.quote_age_seconds for r in chain.rows) == pytest.approx(299)


async def test_contracts_are_listed_and_expire_in_point_in_time(tmp_path: Path) -> None:
    rig = Rig(tmp_path, catalog_refresh_seconds=50)
    name_new, name_old = "BTC_USDC-20OCT26-99999-C", "BTC_USDC-20OCT26-11111-P"

    def mutate(cycle: int, client: FakeClient) -> None:
        if cycle == 0:
            add_instrument(
                client,
                name_old,
                created=START - timedelta(days=1),
                expires=START + timedelta(seconds=45),  # expires between cycles 1 and 2
            )
        if cycle == 1:
            add_instrument(
                client,
                name_new,
                created=START + timedelta(seconds=45),
                expires=START + timedelta(days=2),
            )

    live = await record(rig, 3, mutate=mutate)
    store = ChainStore(tmp_path)
    names = [{r.instrument.name for r in store.as_of(at).rows} for at, _ in live]

    assert name_old in names[0] and name_old in names[1] and name_old not in names[2]
    assert name_new not in names[0] and name_new not in names[1]  # not yet listed
    assert name_new in names[2]
    at_two = store.as_of(live[1][0])
    assert name_new in at_two.quotes_without_instrument  # quoted before the catalog knew it


async def test_asking_outside_the_recording_is_a_clear_error(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    live = await record(rig, 1)
    store = ChainStore(tmp_path)

    with pytest.raises(NoRecordedData):
        store.as_of(live[0][0] - timedelta(hours=3))  # before anything was recorded
    with pytest.raises(NoRecordedData):
        store.as_of(live[0][0] + timedelta(hours=2))  # beyond the quote lookback window
    with pytest.raises(ValueError, match="timezone-aware"):
        store.as_of(datetime(2026, 10, 18, 19, 18))


async def test_recorded_range_reports_first_and_last_receipt(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    assert ChainStore(tmp_path).recorded_range() is None

    live = await record(rig, 3)

    assert ChainStore(tmp_path).recorded_range() == (live[0][0], live[2][0])
