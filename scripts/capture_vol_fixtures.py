"""Capture small real candle and DVOL payloads as test fixtures (read-only public calls).

Run from the repo root:  uv run python scripts/capture_vol_fixtures.py
Writes tests/fixtures/deribit/chart_*.json and dvol_*.json, keeping the JSON-RPC envelope.
"""

import json
import time
from pathlib import Path

import httpx

BASE = "https://www.deribit.com/api/v2/public/"
OUT = Path(__file__).parent.parent / "tests" / "fixtures" / "deribit"
DAY_MS = 86_400_000


def get(client: httpx.Client, method: str, **params: object) -> dict[str, object]:
    response = client.get(BASE + method, params=params)
    response.raise_for_status()
    body: dict[str, object] = response.json()
    return body


def main() -> None:
    now = int(time.time() * 1000)
    files = {}
    with httpx.Client(timeout=30) as client:
        files["chart_btc_usdc_perp_1d.json"] = get(
            client, "get_tradingview_chart_data", instrument_name="BTC_USDC-PERPETUAL",
            resolution="1D", start_timestamp=now - 400 * DAY_MS, end_timestamp=now,
        )  # fmt: skip
        files["chart_btc_usdc_perp_1h.json"] = get(
            client, "get_tradingview_chart_data", instrument_name="BTC_USDC-PERPETUAL",
            resolution="60", start_timestamp=now - 14 * DAY_MS, end_timestamp=now,
        )  # fmt: skip
        files["chart_no_data.json"] = get(
            client, "get_tradingview_chart_data", instrument_name="BTC_USDC-PERPETUAL",
            resolution="60", start_timestamp=1_609_459_200_000, end_timestamp=1_612_000_000_000,
        )  # fmt: skip
        files["dvol_btc_1d.json"] = get(
            client, "get_volatility_index_data", currency="BTC", resolution="1D",
            start_timestamp=now - 400 * DAY_MS, end_timestamp=now,
        )  # fmt: skip
        files["dvol_btc_1h_page.json"] = get(
            client, "get_volatility_index_data", currency="BTC", resolution="3600",
            start_timestamp=now - 60 * DAY_MS, end_timestamp=now,
        )  # fmt: skip
    for name, body in files.items():
        (OUT / name).write_text(json.dumps(body, indent=None) + "\n", encoding="utf-8")
        result = body["result"]
        size = len(result["ticks"]) if "ticks" in result else len(result["data"])  # type: ignore[index, operator]
        print(name, size, "rows", (OUT / name).stat().st_size, "bytes")


if __name__ == "__main__":
    main()
