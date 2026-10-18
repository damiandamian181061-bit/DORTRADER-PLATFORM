"""Capture trimmed real Deribit payloads as test fixtures (read-only public calls).

Run from the repo root:  uv run python scripts/capture_deribit_fixtures.py
Writes tests/fixtures/deribit/. Fixtures keep the original JSON-RPC envelope so the
client's parsing of usIn/usOut is exercised by tests.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

BASE = "https://www.deribit.com/api/v2/public/"
OUT = Path(__file__).parent.parent / "tests" / "fixtures" / "deribit"
BAND = 0.08  # keep strikes within +-8% of the forward for the chosen expiries
WING_COUNT = 6  # plus a few far strikes, which are often one-sided or empty


def get(client: httpx.Client, method: str, **params: object) -> dict[str, object]:
    response = client.get(BASE + method, params=params)
    response.raise_for_status()
    body: dict[str, object] = response.json()
    return body


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=30) as client:
        instruments = get(client, "get_instruments", currency="USDC", kind="option")
        summary = get(client, "get_book_summary_by_currency", currency="USDC", kind="option")
        now_ms = int(datetime.now(UTC).timestamp() * 1000)

        btc = [i for i in instruments["result"] if i["base_currency"] == "BTC"]  # type: ignore[attr-defined, union-attr]
        expiries = sorted(
            {
                i["expiration_timestamp"]
                for i in btc
                if i["expiration_timestamp"] > now_ms + 86_400_000
            }
        )
        chosen = expiries[:2]
        rows = {r["instrument_name"]: r for r in summary["result"]}  # type: ignore[attr-defined, union-attr]
        forward = {
            i["expiration_timestamp"]: rows[i["instrument_name"]]["underlying_price"]
            for i in btc
            if i["instrument_name"] in rows and rows[i["instrument_name"]].get("underlying_price")
        }

        keep: list[str] = []
        wings: list[str] = []
        for inst in btc:
            exp = inst["expiration_timestamp"]
            if exp not in chosen or exp not in forward:
                continue
            if abs(inst["strike"] / forward[exp] - 1) <= BAND:
                keep.append(inst["instrument_name"])
            else:
                wings.append(inst["instrument_name"])
        keep += wings[:: max(1, len(wings) // WING_COUNT)][:WING_COUNT]
        other = [
            i["instrument_name"] for i in instruments["result"] if i["base_currency"] == "SOL"
        ][:3]  # type: ignore[attr-defined, union-attr]
        names = set(keep) | set(other)

        instruments["result"] = [i for i in instruments["result"] if i["instrument_name"] in names]  # type: ignore[attr-defined, union-attr]
        summary["result"] = [r for r in summary["result"] if r["instrument_name"] in names]  # type: ignore[attr-defined, union-attr]

        nearest = chosen[0]
        atm = min(
            (i for i in btc if i["expiration_timestamp"] == nearest and i["option_type"] == "put"),
            key=lambda i: abs(i["strike"] - forward[nearest]),
        )["instrument_name"]
        wing = wings[0]
        files = {
            "instruments_usdc_option.json": instruments,
            "book_summary_usdc_option.json": summary,
            "ticker_atm_put.json": get(client, "ticker", instrument_name=atm),
            "ticker_far_wing.json": get(client, "ticker", instrument_name=wing),
            "order_book_atm_put.json": get(client, "get_order_book", instrument_name=atm, depth=5),
        }

    for name, body in files.items():
        (OUT / name).write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
    manifest = {
        "captured_at": datetime.now(UTC).isoformat(),
        "source": BASE,
        "atm_put": atm,
        "far_wing": wing,
        "instrument_count": len(names),
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    main()
