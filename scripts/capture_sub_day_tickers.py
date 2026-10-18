"""Capture tickers for options expiring within a day (theta behaves differently there).

Run from the repo root:  uv run python scripts/capture_sub_day_tickers.py
Needs an expiry less than 24h away; Deribit lists dailies, so run it the day before one.
Writes tests/fixtures/deribit/ticker_sub_day_*.json plus the expiry it used.
"""

import json
import time
from pathlib import Path

import httpx

BASE = "https://www.deribit.com/api/v2/public/"
OUT = Path(__file__).parent.parent / "tests" / "fixtures" / "deribit"


def main() -> None:
    with httpx.Client(timeout=30) as client:
        instruments = [
            i
            for i in client.get(
                BASE + "get_instruments", params={"currency": "USDC", "kind": "option"}
            ).json()["result"]
            if i["base_currency"] == "BTC"
        ]
        now_ms = time.time() * 1000
        expiry = min(
            i["expiration_timestamp"] for i in instruments if i["expiration_timestamp"] > now_ms
        )
        if expiry - now_ms > 86_400_000:
            raise SystemExit("nearest expiry is more than 24h away; try again later")
        calls = sorted(
            (
                i
                for i in instruments
                if i["expiration_timestamp"] == expiry and i["option_type"] == "call"
            ),
            key=lambda i: i["strike"],
        )
        tickers = [
            client.get(BASE + "ticker", params={"instrument_name": i["instrument_name"]}).json()
            for i in calls
        ]
        usable = [t for t in tickers if t["result"].get("greeks") and t["result"].get("mark_iv")]
        forward = usable[0]["result"]["underlying_price"]
        by_distance = sorted(
            usable, key=lambda t: abs(float(t["result"]["instrument_name"].split("-")[2]) - forward)
        )
        atm = by_distance[0]
        otm = max(
            (
                t
                for t in usable
                if float(t["result"]["instrument_name"].split("-")[2]) > forward * 1.003
            ),
            key=lambda t: (
                t["result"]["mark_price"] > 20
                and -float(t["result"]["instrument_name"].split("-")[2])
            ),
        )
    (OUT / "ticker_sub_day_atm_call.json").write_text(
        json.dumps(atm, indent=1) + "\n", encoding="utf-8"
    )
    (OUT / "ticker_sub_day_otm_call.json").write_text(
        json.dumps(otm, indent=1) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "expiry_ms": expiry,
                "atm": atm["result"]["instrument_name"],
                "otm": otm["result"]["instrument_name"],
            }
        )
    )


if __name__ == "__main__":
    main()
