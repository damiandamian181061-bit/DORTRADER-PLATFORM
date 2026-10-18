import hashlib
import json
from datetime import UTC, datetime

import httpx
import pytest

from dortrader.clock import ManualClock
from dortrader.config import DeribitSettings
from dortrader.market_data.deribit.rest import DeribitError, DeribitRestClient

pytestmark = pytest.mark.anyio

START = datetime(2026, 10, 18, 19, 0, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Harness:
    """A client wired to a scripted transport and a clock that only moves when it sleeps."""

    def __init__(self, handler: httpx.MockTransport, **settings: float) -> None:
        self.clock = ManualClock(START)
        self.sleeps: list[float] = []
        self.client = DeribitRestClient(
            DeribitSettings(**settings),
            self.clock,
            transport=handler,
            sleep=self._sleep,
            jitter=lambda: 0.5,
        )

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.clock.advance(seconds)


def ok_body(result: object = None, us_out: int | None = None) -> bytes:
    body: dict[str, object] = {"jsonrpc": "2.0", "result": [] if result is None else result}
    if us_out is not None:
        body["usOut"] = us_out
    return json.dumps(body).encode()


async def test_success_returns_result_hash_and_server_time() -> None:
    server_us = int(START.timestamp() * 1_000_000) + 250_000
    body = ok_body([{"instrument_name": "X"}], server_us)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=body)

    harness = Harness(httpx.MockTransport(handler))
    envelope = await harness.client.get_book_summary_by_currency("USDC", "option")

    assert envelope.result == [{"instrument_name": "X"}]
    assert envelope.body_sha256 == hashlib.sha256(body).hexdigest()
    assert envelope.server_time is not None and envelope.server_time.microsecond == 250_000
    assert envelope.clock_offset_seconds == pytest.approx(0.25)
    assert seen[0].url.path == "/api/v2/public/get_book_summary_by_currency"
    assert dict(seen[0].url.params) == {"currency": "USDC", "kind": "option"}


async def test_transient_http_errors_are_retried_with_backoff() -> None:
    responses = [httpx.Response(502), httpx.Response(429), httpx.Response(200, content=ok_body())]

    harness = Harness(httpx.MockTransport(lambda request: responses.pop(0)))
    envelope = await harness.client.get_index_price("btc_usdc")

    assert envelope.result == []
    assert harness.sleeps[-2:] == [0.5, 1.0]  # base 0.5 x 2**attempt x (0.5 + jitter 0.5)


async def test_too_many_requests_error_code_is_retried() -> None:
    limited = httpx.Response(200, json={"error": {"code": 10028, "message": "too_many_requests"}})
    responses = [limited, httpx.Response(200, content=ok_body())]

    harness = Harness(httpx.MockTransport(lambda request: responses.pop(0)))

    assert (await harness.client.get_index_price("btc_usdc")).result == []


async def test_exchange_errors_are_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"error": {"code": 11050, "message": "bad_request"}})

    harness = Harness(httpx.MockTransport(handler))

    with pytest.raises(DeribitError) as caught:
        await harness.client.get_index_price("nope")
    assert caught.value.code == 11050
    assert calls == 1


async def test_gives_up_after_max_retries() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("boom")

    harness = Harness(httpx.MockTransport(handler), max_retries=2)

    with pytest.raises(DeribitError, match="after 3 attempts"):
        await harness.client.get_index_price("btc_usdc")
    assert calls == 3


async def test_calls_to_the_same_method_are_spaced() -> None:
    harness = Harness(
        httpx.MockTransport(lambda request: httpx.Response(200, content=ok_body())),
        min_interval_seconds=2.0,
        instruments_min_interval_seconds=60.0,
    )

    await harness.client.get_index_price("btc_usdc")
    await harness.client.get_index_price("btc_usdc")
    await harness.client.get_instruments("USDC", "option")
    await harness.client.get_instruments("USDC", "option")

    assert harness.sleeps == [2.0, 60.0]


async def test_non_json_body_is_an_error() -> None:
    harness = Harness(httpx.MockTransport(lambda request: httpx.Response(200, content=b"<html>")))

    with pytest.raises(DeribitError, match="non-JSON"):
        await harness.client.get_index_price("btc_usdc")
