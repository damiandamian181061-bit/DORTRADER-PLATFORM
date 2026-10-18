"""Arrow schemas and row conversions for the datasets the recorder writes."""

import json
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

import pyarrow as pa

from dortrader.domain import Instrument, OptionQuote, QualityFlag, TickStep

TS = pa.timestamp("us", tz="UTC")
DEC = pa.decimal128(28, 10)

QUOTES_VERSION = 1
QUOTES_SCHEMA = pa.schema(
    [
        ("received_at", TS),
        ("exchange_ts", TS),
        ("instrument_name", pa.string()),
        ("source", pa.string()),
        ("channel", pa.string()),
        ("bid_price", pa.float64()),
        ("ask_price", pa.float64()),
        ("mark_price", pa.float64()),
        ("mark_iv", pa.float64()),
        ("underlying_price", pa.float64()),
        ("underlying_index", pa.string()),
        ("interest_rate", pa.float64()),
        ("open_interest", pa.float64()),
        ("volume", pa.float64()),
        ("last_price", pa.float64()),
        ("flags", pa.list_(pa.string())),
        ("raw_sha256", pa.string()),
        ("connection_epoch", pa.int32()),
    ]
)

RAW_VERSION = 1
RAW_SCHEMA = pa.schema(
    [
        ("received_at", TS),
        ("exchange_ts", TS),
        ("run_id", pa.string()),
        ("connection_epoch", pa.int32()),
        ("request_sha256", pa.string()),  # hash of the full HTTP response body
        ("fragment_sha256", pa.string()),  # hash of `payload` below
        ("instrument_count", pa.int32()),
        ("payload", pa.string()),  # the rows kept from the response, as JSON
    ]
)

CATALOG_VERSION = 1
CATALOG_SCHEMA = pa.schema(
    [
        ("snapshot_at", TS),
        ("instrument_id", pa.int64()),
        ("name", pa.string()),
        ("base_currency", pa.string()),
        ("quote_currency", pa.string()),
        ("settlement_currency", pa.string()),
        ("instrument_type", pa.string()),
        ("option_type", pa.string()),
        ("strike", DEC),
        ("expiration", TS),
        ("created_at", TS),
        ("contract_size", DEC),
        ("tick_size", DEC),
        ("tick_size_steps", pa.string()),  # JSON: [{"above_price": "1000", "tick_size": "20"}]
        ("min_trade_amount", DEC),
        ("state", pa.string()),
        ("is_active", pa.bool_()),
        ("price_index", pa.string()),
        ("maker_commission", DEC),
        ("taker_commission", DEC),
    ]
)

EVENTS_VERSION = 1
EVENTS_SCHEMA = pa.schema(
    [
        ("at", TS),
        ("run_id", pa.string()),
        ("kind", pa.string()),  # gap | error | schema_drift | catalog_change | cycle
        ("detail", pa.string()),  # JSON
    ]
)


def quotes_to_table(quotes: Sequence[OptionQuote]) -> pa.Table:
    columns: dict[str, list[Any]] = {name: [] for name in QUOTES_SCHEMA.names}
    for q in quotes:
        row = q.model_dump()
        row["flags"] = [flag.value for flag in q.flags]
        for name in columns:
            columns[name].append(row[name])
    return pa.table(columns, schema=QUOTES_SCHEMA)


def table_to_quotes(table: pa.Table) -> list[OptionQuote]:
    rows = table.to_pylist()
    return [OptionQuote(**{**r, "flags": tuple(QualityFlag(f) for f in r["flags"])}) for r in rows]


def instruments_to_table(snapshot_at: datetime, instruments: Sequence[Instrument]) -> pa.Table:
    rows = []
    for i in instruments:
        steps = [
            {"above_price": str(s.above_price), "tick_size": str(s.tick_size)}
            for s in i.tick_size_steps
        ]
        rows.append(
            {
                "snapshot_at": snapshot_at,
                "instrument_id": i.instrument_id,
                "name": i.name,
                "base_currency": i.base_currency,
                "quote_currency": i.quote_currency,
                "settlement_currency": i.settlement_currency,
                "instrument_type": i.instrument_type,
                "option_type": i.option_type,
                "strike": i.strike,
                "expiration": i.expiration,
                "created_at": i.created_at,
                "contract_size": i.contract_size,
                "tick_size": i.tick_size,
                "tick_size_steps": json.dumps(steps),
                "min_trade_amount": i.min_trade_amount,
                "state": i.state,
                "is_active": i.is_active,
                "price_index": i.price_index,
                "maker_commission": i.maker_commission,
                "taker_commission": i.taker_commission,
            }
        )
    return pa.Table.from_pylist(rows, schema=CATALOG_SCHEMA)


def table_to_instruments(table: pa.Table) -> list[Instrument]:
    instruments = []
    for r in table.to_pylist():
        steps = tuple(
            TickStep(above_price=Decimal(d["above_price"]), tick_size=Decimal(d["tick_size"]))
            for d in json.loads(r["tick_size_steps"])
        )
        fields = {k: v for k, v in r.items() if k != "snapshot_at"}
        instruments.append(Instrument(**{**fields, "tick_size_steps": steps}))
    return instruments


def event_row(at: datetime, run_id: str, kind: str, detail: dict[str, object]) -> pa.Table:
    return pa.Table.from_pylist(
        [{"at": at, "run_id": run_id, "kind": kind, "detail": json.dumps(detail, default=str)}],
        schema=EVENTS_SCHEMA,
    )
