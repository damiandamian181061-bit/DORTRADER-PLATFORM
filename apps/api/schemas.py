"""JSON shapes the web app depends on."""

from datetime import datetime

from pydantic import BaseModel


class ContractView(BaseModel):
    instrument_name: str
    bid: float | None
    ask: float | None
    mark: float
    quote_age_seconds: float
    open_interest: float
    bid_iv: float | None
    ask_iv: float | None
    mid_iv: float | None
    mark_iv: float | None  # ours, from the mark price
    deribit_mark_iv: float | None
    iv_diff_points: float | None
    iv_spread_points: float | None
    delta: float | None
    gamma: float | None
    vega: float | None
    theta: float | None
    flags: list[str]  # quote, snapshot and arbitrage flags together


class StrikeView(BaseModel):
    strike: float
    call: ContractView | None
    put: ContractView | None


class ExpiryView(BaseModel):
    expiration: datetime
    forward: float | None
    years_to_expiry: float
    strikes: list[StrikeView]


class ChainResponse(BaseModel):
    snapshot_at: datetime
    model_version: str
    inputs_hash: str
    listed_without_quote: int
    quotes_without_instrument: int
    expiries: list[ExpiryView]


class GapView(BaseModel):
    start: datetime
    end: datetime
    seconds: float


class StatusResponse(BaseModel):
    now: datetime
    healthy: bool
    last_good_at: datetime | None
    age_seconds: float | None
    quotes_per_cycle: int | None
    run_id: str | None
    recorded_from: datetime | None
    recorded_to: datetime | None
    recent_gaps: list[GapView]
