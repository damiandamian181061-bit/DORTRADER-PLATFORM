"""Throwaway probe: which public WebSocket channels exist for BTC/USDC options, and how busy are they?

Run:  uv run python scripts/probe_deribit_ws.py [seconds]
Read-only, unauthenticated. Prints one line per candidate channel.
"""

import asyncio
import json
import sys
import time
from collections import Counter
from typing import Any

import httpx
from websockets.asyncio.client import ClientConnection, connect

REST = "https://www.deribit.com/api/v2/public/"
WS = "wss://www.deribit.com/ws/api/v2"


def pick_instrument() -> str:
    """The BTC/USDC option with the most open interest among the nearest expiry (>1 day away)."""
    rows = httpx.get(
        REST + "get_book_summary_by_currency", params={"currency": "USDC", "kind": "option"}
    ).json()["result"]
    btc = [r for r in rows if r["instrument_name"].startswith("BTC_USDC-") and r.get("bid_price")]
    now_ms = time.time() * 1000
    instruments = httpx.get(
        REST + "get_instruments", params={"currency": "USDC", "kind": "option"}
    ).json()["result"]
    expiry = {i["instrument_name"]: i["expiration_timestamp"] for i in instruments}
    eligible = [r for r in btc if expiry[r["instrument_name"]] > now_ms + 86_400_000]
    nearest = min(expiry[r["instrument_name"]] for r in eligible)
    best = max(
        (r for r in eligible if expiry[r["instrument_name"]] == nearest),
        key=lambda r: r["open_interest"],
    )
    return str(best["instrument_name"])


async def main(listen_seconds: float) -> None:
    inst = pick_instrument()
    candidates = [
        f"ticker.{inst}.raw",
        f"ticker.{inst}.100ms",
        f"ticker.{inst}.agg2",
        f"incremental_ticker.{inst}",
        f"quote.{inst}",
        f"book.{inst}.none.10.100ms",
        f"book.{inst}.none.10.agg2",
        f"book.{inst}.agg2",
        "markprice.options.btc_usdc",
        "deribit_price_index.btc_usdc",
        "instrument.state.option.USDC",
        "instrument.state.any.USDC",
        "instrument.creation.option.USDC",
        "trades.option.USDC.100ms",
        "trades.option.USDC.agg2",
        "estimated_expiration_price.btc_usdc",
    ]
    msgs: Counter[str] = Counter()
    size: Counter[str] = Counter()
    sample: dict[str, str] = {}
    status: dict[str, str] = {}
    rid = 0

    def record(message: dict[str, Any], text: str) -> None:
        channel = message["params"]["channel"]
        msgs[channel] += 1
        size[channel] += len(text)
        sample.setdefault(channel, json.dumps(message["params"]["data"])[:160])

    async def request(ws: ClientConnection, method: str, params: dict[str, object]) -> Any:
        nonlocal rid
        rid += 1
        my_id = rid
        await ws.send(
            json.dumps({"jsonrpc": "2.0", "id": my_id, "method": method, "params": params})
        )
        while True:
            text = await ws.recv()
            text = text if isinstance(text, str) else text.decode()
            message = json.loads(text)
            if message.get("id") == my_id:
                return message
            if message.get("method") == "subscription":
                record(message, text)

    async with connect(WS, max_size=None) as ws:
        await request(ws, "public/set_heartbeat", {"interval": 15})
        for channel in candidates:
            reply = await request(ws, "public/subscribe", {"channels": [channel]})
            status[channel] = "error " + json.dumps(reply["error"]) if "error" in reply else "ok"
            await asyncio.sleep(0.4)

        deadline = time.monotonic() + listen_seconds
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2)
            except TimeoutError:
                continue
            text = raw if isinstance(raw, str) else raw.decode()
            message = json.loads(text)
            if message.get("method") == "heartbeat" and message["params"]["type"] == "test_request":
                await request(ws, "public/test", {})
            elif message.get("method") == "subscription":
                record(message, text)

    print(f"instrument={inst} listened={listen_seconds}s (plus subscribe phase)")
    for channel in candidates:
        n = msgs[channel]
        print(
            f"{channel:52s} {status[channel][:75]:75s} msgs={n:5d} avg_bytes={size[channel] // max(n, 1):7d}"
        )
        if n:
            print(f"    sample: {sample[channel]}")


if __name__ == "__main__":
    asyncio.run(main(float(sys.argv[1]) if len(sys.argv) > 1 else 40))
