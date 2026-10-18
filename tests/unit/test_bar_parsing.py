import copy
import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from dortrader.clock import ManualClock
from dortrader.config import DeribitSettings
from dortrader.domain import Bar
from dortrader.market_data.deribit.models import SchemaDriftError
from dortrader.market_data.deribit.rest import DeribitRestClient
from dortrader.market_data.normalize import parse_chart, parse_vol_index
from tests.conftest import load_json

pytestmark = pytest.mark.anyio

DAY, HOUR = 86_400, 3_600


def chart(name: str) -> dict:  # type: ignore[type-arg]
    return load_json("deribit", name)["result"]  # type: ignore[no-any-return]


def test_daily_candles_parse_and_start_at_08_utc() -> None:
    parsed = parse_chart(chart("chart_btc_usdc_perp_1d.json"), instrument="X", step_seconds=DAY)

    assert len(parsed.items) == 401 and parsed.unexpected_fields == frozenset()
    assert all(b.start.hour == 8 and b.start.minute == 0 for b in parsed.items)  # Deribit's day
    assert all(b.high >= b.low and b.volume is not None for b in parsed.items)
    starts = [b.start for b in parsed.items]
    assert starts == sorted(set(starts))


def test_hourly_candles_parse_on_the_hour() -> None:
    parsed = parse_chart(chart("chart_btc_usdc_perp_1h.json"), instrument="X", step_seconds=HOUR)

    assert len(parsed.items) == 337
    assert all(b.start.minute == 0 and b.step_seconds == HOUR for b in parsed.items)


def test_no_data_is_an_empty_valid_answer() -> None:
    parsed = parse_chart(chart("chart_no_data.json"), instrument="X", step_seconds=HOUR)

    assert parsed.items == [] and parsed.unexpected_fields == frozenset()


def test_dvol_parses_without_volume_and_reports_the_continuation() -> None:
    daily, daily_next = parse_vol_index(
        chart("dvol_btc_1d.json"), name="DVOL-BTC", step_seconds=DAY
    )
    page, page_next = parse_vol_index(
        chart("dvol_btc_1h_page.json"), name="DVOL-BTC", step_seconds=HOUR
    )

    assert len(daily.items) == 401 and daily_next is None
    assert all(b.volume is None and b.instrument == "DVOL-BTC" for b in daily.items)
    assert len(page.items) == 1000 and isinstance(page_next, int)  # more history behind it
    assert 5 < min(b.close for b in daily.items) < max(b.close for b in daily.items) < 300


def test_array_length_mismatch_is_schema_drift() -> None:
    broken = copy.deepcopy(chart("chart_btc_usdc_perp_1h.json"))
    broken["volume"] = broken["volume"][:-1]

    with pytest.raises(SchemaDriftError, match="different lengths"):
        parse_chart(broken, instrument="X", step_seconds=HOUR)


def test_a_missing_field_and_a_malformed_dvol_row_are_schema_drift() -> None:
    missing = {k: v for k, v in chart("chart_btc_usdc_perp_1h.json").items() if k != "status"}
    with pytest.raises(SchemaDriftError):
        parse_chart(missing, instrument="X", step_seconds=HOUR)
    with pytest.raises(SchemaDriftError, match="expected 5"):
        parse_vol_index({"data": [[1, 2, 3]], "continuation": None}, name="D", step_seconds=DAY)


def test_a_new_response_field_is_reported_not_fatal() -> None:
    extra = {**chart("chart_btc_usdc_perp_1h.json"), "brand_new": 1}

    parsed = parse_chart(extra, instrument="X", step_seconds=HOUR)

    assert parsed.unexpected_fields == frozenset({"brand_new"})


def test_bar_rejects_high_below_low_but_keeps_odd_open_and_close() -> None:
    start = datetime(2026, 10, 18, tzinfo=UTC)
    with pytest.raises(ValidationError, match="high is below low"):
        Bar(instrument="X", step_seconds=60, start=start, open=1, high=1, low=2, close=1)
    odd = Bar(instrument="X", step_seconds=60, start=start, open=5, high=3, low=2, close=1)
    assert odd.open == 5  # recorded as received


async def test_client_sends_the_endpoint_specific_resolution_strings() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=json.dumps({"result": {"status": "no_data"}}))

    client = DeribitRestClient(
        DeribitSettings(), ManualClock(datetime(2026, 10, 18, tzinfo=UTC)),
        transport=httpx.MockTransport(handler),
    )  # fmt: skip

    await client.get_tradingview_chart_data("BTC-PERPETUAL", 3600, 1_000, 2_000)
    await client.get_tradingview_chart_data("BTC-PERPETUAL", 86_400, 1_000, 2_000)
    await client.get_volatility_index_data("BTC", 3600, 1_000, 2_000)
    await client.get_volatility_index_data("BTC", 86_400, 1_000, 2_000)

    resolutions = [(r.url.path.rsplit("/", 1)[1], r.url.params["resolution"]) for r in seen]
    assert resolutions == [
        ("get_tradingview_chart_data", "60"),
        ("get_tradingview_chart_data", "1D"),
        ("get_volatility_index_data", "3600"),
        ("get_volatility_index_data", "1D"),
    ]
    assert seen[0].url.params["start_timestamp"] == "1000"
    assert seen[0].url.params["instrument_name"] == "BTC-PERPETUAL"
