import asyncio
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dortrader.domain import QualityFlag
from dortrader.market_data.deribit.rest import DeribitError, Envelope
from dortrader.storage.parquet_archive import read_archive, verify_archive
from dortrader.storage.tables import (
    QUOTES_VERSION,
    RAW_VERSION,
    table_to_quotes,
)
from tests.helpers import Rig

pytestmark = pytest.mark.anyio


async def test_one_cycle_archives_raw_payload_and_normalized_quotes(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    outcome = await rig.recorder.run_cycle()
    rig.recorder.close()

    assert outcome.quotes == 88  # BTC rows only; the 3 SOL rows are excluded
    quotes_dir = tmp_path / "normalized" / "source=deribit" / "channel=book_summary"
    quotes = table_to_quotes(read_archive(quotes_dir, schema_version=QUOTES_VERSION))
    assert len(quotes) == 88
    assert all(q.instrument_name.startswith("BTC_USDC-") for q in quotes)
    assert any(QualityFlag.NO_BID in q.flags for q in quotes)

    raw_dir = tmp_path / "raw" / "source=deribit" / "channel=book_summary"
    raw = read_archive(raw_dir, schema_version=RAW_VERSION).to_pylist()
    assert len(raw) == 1
    payload = raw[0]["payload"]
    assert raw[0]["fragment_sha256"] == hashlib.sha256(payload.encode()).hexdigest()
    assert raw[0]["instrument_count"] == 88 == len(json.loads(payload))
    assert raw[0]["request_sha256"] == quotes[0].raw_sha256  # lineage from quote back to response
    assert verify_archive(raw_dir).ok and verify_archive(quotes_dir).ok


async def test_catalog_is_loaded_once_and_snapshotted(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    await rig.recorder.run_cycle()
    rig.clock.advance(30)
    await rig.recorder.run_cycle()

    assert rig.client.instrument_calls == 1
    assert rig.client.summary_calls == 2
    assert len(rig.recorder.catalog) == 88
    rig.recorder.close()
    catalog = read_archive(tmp_path / "catalog" / "source=deribit", schema_version=1)
    assert catalog.num_rows == 88  # one snapshot, unchanged catalog is not rewritten


async def test_catalog_changes_are_recorded_as_events(tmp_path: Path) -> None:
    rig = Rig(tmp_path, catalog_refresh_seconds=100)
    await rig.recorder.run_cycle()
    victim = next(i for i in rig.client.instruments if i["base_currency"] == "BTC")
    rig.client.instruments.remove(victim)

    rig.clock.advance(150)
    await rig.recorder.run_cycle()

    changes = [e for e in rig.events() if e["kind"] == "catalog_change"]
    assert [c["detail"]["removed"] for c in changes] == [[victim["instrument_name"]]]


async def test_missed_cycles_are_recorded_as_a_gap(tmp_path: Path) -> None:
    rig = Rig(tmp_path, poll_interval_seconds=30)
    await rig.recorder.run_cycle()
    rig.clock.advance(31)
    await rig.recorder.run_cycle()  # within 2.5 intervals: no gap
    rig.clock.advance(200)
    await rig.recorder.run_cycle()

    gaps = [e for e in rig.events() if e["kind"] == "gap"]
    assert len(gaps) == 1 and gaps[0]["detail"]["seconds"] == pytest.approx(200)


async def test_clock_drift_is_flagged_on_every_quote(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.client.clock_offset = 5.0

    outcome = await rig.recorder.run_cycle()
    rig.recorder.close()

    assert outcome.clock_offset_seconds == 5.0

    quotes_dir = tmp_path / "normalized" / "source=deribit" / "channel=book_summary"
    quotes = table_to_quotes(read_archive(quotes_dir, schema_version=QUOTES_VERSION))
    assert all(QualityFlag.CLOCK_DRIFT in q.flags for q in quotes)


async def test_stale_summary_offset_never_triggers_clock_drift(tmp_path: Path) -> None:
    rig = Rig(tmp_path)  # index offset 0.01 s; the summary reports a stale 1.5 s

    outcome = await rig.recorder.run_cycle()
    rig.recorder.close()

    assert outcome.clock_offset_seconds == 0.01 and rig.client.index_calls == 1


async def test_recording_survives_the_clock_probe_failing(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    async def broken(index_name: str) -> Envelope:
        raise DeribitError(-1, "down")

    rig.client.get_index_price = broken  # type: ignore[method-assign]

    outcome = await rig.recorder.run_cycle()

    assert outcome.quotes == 88 and outcome.clock_offset_seconds is None


async def test_new_exchange_field_is_reported_once(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    for row in rig.client.summary:
        row["brand_new_field"] = 1

    await rig.recorder.run_cycle()
    rig.clock.advance(30)
    await rig.recorder.run_cycle()

    drift = [e for e in rig.events() if e["kind"] == "schema_drift"]
    assert len(drift) == 1 and drift[0]["detail"]["unexpected_fields"] == ["brand_new_field"]


async def test_a_failing_cycle_is_recorded_and_recording_continues(tmp_path: Path) -> None:
    rig = Rig(tmp_path, poll_interval_seconds=30)
    rig.client.summary_error = DeribitError(10028, "too_many_requests")
    rig.client.summary_failures = 1

    await rig.recorder.run(asyncio.Event(), max_cycles=2)

    assert rig.client.summary_calls == 2
    kinds = Counter(e["kind"] for e in rig.events())
    assert kinds["error"] == 1
    quotes_dir = tmp_path / "normalized" / "source=deribit" / "channel=book_summary"
    assert read_archive(quotes_dir, schema_version=QUOTES_VERSION).num_rows == 88


async def test_start_recovers_files_a_crash_left_behind(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    orphan = tmp_path / "events" / "date=2026-10-18" / "hour=19" / "part-old-00000.parquet.tmp"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"torn write")

    rig.recorder.start()

    assert not orphan.exists()
    assert (tmp_path / "events" / "_quarantine" / orphan.name).exists()
    assert datetime(2026, 10, 18, tzinfo=UTC) < rig.clock.now()
