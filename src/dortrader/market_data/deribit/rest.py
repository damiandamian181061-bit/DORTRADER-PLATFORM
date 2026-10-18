"""Async client for Deribit's public JSON-RPC-over-HTTP market data.

Keeps the raw response bytes' hash with every result so recordings stay traceable. Calls are
spaced per method and retried with jittered backoff. Public data only: no credentials.
"""

import asyncio
import hashlib
import json
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from dortrader.clock import Clock
from dortrader.config import DeribitSettings
from dortrader.domain import us_to_utc

Sleep = Callable[[float], Awaitable[None]]
TOO_MANY_REQUESTS = 10028
# The two endpoints name the same candle sizes differently: seconds in, their strings out.
CHART_RESOLUTIONS = {60: "1", 300: "5", 900: "15", 3600: "60", 86_400: "1D"}
VOL_INDEX_RESOLUTIONS = {60: "60", 3600: "3600", 43_200: "43200", 86_400: "1D"}


class DeribitError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"deribit error {code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Envelope:
    result: Any
    body_sha256: str
    received_at: datetime
    latency_seconds: float
    server_time: datetime | None  # from the exchange's usOut
    clock_offset_seconds: float | None  # server minus local, around the request midpoint


class DeribitRestClient:
    def __init__(
        self,
        settings: DeribitSettings,
        clock: Clock,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter
        self._last_call: dict[str, float] = {}
        self._http = httpx.AsyncClient(
            base_url=settings.base_url, timeout=settings.timeout_seconds, transport=transport
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_instruments(self, currency: str, kind: str) -> Envelope:
        return await self._call("get_instruments", {"currency": currency, "kind": kind})

    async def get_book_summary_by_currency(self, currency: str, kind: str) -> Envelope:
        return await self._call(
            "get_book_summary_by_currency", {"currency": currency, "kind": kind}
        )

    async def get_index_price(self, index_name: str) -> Envelope:
        return await self._call("get_index_price", {"index_name": index_name})

    async def get_tradingview_chart_data(
        self, instrument_name: str, step_seconds: int, start_ms: int, end_ms: int
    ) -> Envelope:
        return await self._call(
            "get_tradingview_chart_data",
            {
                "instrument_name": instrument_name,
                "resolution": CHART_RESOLUTIONS[step_seconds],
                "start_timestamp": str(start_ms),
                "end_timestamp": str(end_ms),
            },
        )

    async def get_volatility_index_data(
        self, currency: str, step_seconds: int, start_ms: int, end_ms: int
    ) -> Envelope:
        return await self._call(
            "get_volatility_index_data",
            {
                "currency": currency,
                "resolution": VOL_INDEX_RESOLUTIONS[step_seconds],
                "start_timestamp": str(start_ms),
                "end_timestamp": str(end_ms),
            },
        )

    async def _throttle(self, method: str) -> None:
        spacing = (
            self._settings.instruments_min_interval_seconds
            if method == "get_instruments"
            else self._settings.min_interval_seconds
        )
        last = self._last_call.get(method)
        if last is not None:
            wait = spacing - (self._clock.monotonic() - last)
            if wait > 0:
                await self._sleep(wait)
        self._last_call[method] = self._clock.monotonic()

    async def _call(self, method: str, params: dict[str, str]) -> Envelope:
        attempt = 0
        while True:
            await self._throttle(method)
            try:
                return await self._once(method, params)
            except (httpx.TransportError, _Retryable) as error:
                if attempt >= self._settings.max_retries:
                    raise DeribitError(
                        -1, f"{method} failed after {attempt + 1} attempts: {error}"
                    ) from error
                await self._sleep(min(30.0, 0.5 * 2**attempt) * (0.5 + self._jitter()))
                attempt += 1

    async def _once(self, method: str, params: dict[str, str]) -> Envelope:
        started_wall = self._clock.now()
        started = self._clock.monotonic()
        response = await self._http.get(f"/public/{method}", params=params)
        latency = self._clock.monotonic() - started
        received_at = self._clock.now()
        if response.status_code == 429 or response.status_code >= 500:
            raise _Retryable(f"http {response.status_code}")
        body_bytes = response.content
        try:
            body = json.loads(body_bytes)
        except ValueError as error:
            raise DeribitError(
                -1, f"{method} returned non-JSON (http {response.status_code})"
            ) from error
        if "error" in body:
            err = body["error"]
            if err.get("code") == TOO_MANY_REQUESTS:
                raise _Retryable("too_many_requests")
            raise DeribitError(int(err.get("code", -1)), str(err.get("message", "")))
        if "result" not in body:
            raise DeribitError(-1, f"{method} response has no result")
        server_time = us_to_utc(int(body["usOut"])) if "usOut" in body else None
        offset = None
        if server_time is not None:
            midpoint = started_wall + (received_at - started_wall) / 2
            offset = (server_time - midpoint).total_seconds()
        return Envelope(
            result=body["result"],
            body_sha256=hashlib.sha256(body_bytes).hexdigest(),
            received_at=received_at,
            latency_seconds=latency,
            server_time=server_time,
            clock_offset_seconds=offset,
        )


class _Retryable(Exception):
    pass
