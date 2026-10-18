"""Price a whole chain snapshot: implied vols from every quote, Greeks, and no-arbitrage flags.

Pure and deterministic: the same snapshot always gives the same result, and the result carries
the model version and a hash of its inputs.
"""

import hashlib
import json
import math

import numpy as np

from dortrader.domain import ChainSnapshot, FrozenModel, QualityFlag, UtcDateTime
from dortrader.pricing.arbitrage import find_violations
from dortrader.pricing.black76 import greeks
from dortrader.pricing.conventions import PRICING_MODEL_VERSION, time_to_expiry_years
from dortrader.pricing.implied_vol import IVStatus, implied_vol


class ContractValuation(FrozenModel):
    instrument_name: str
    option_type: str
    strike: float
    expiration: UtcDateTime
    years_to_expiry: float
    forward: float | None
    log_moneyness: float | None  # ln(K / F)
    # Our implied vols (decimal) from each price; None when the solver reports anything but OK.
    bid_iv: float | None
    ask_iv: float | None
    mid_iv: float | None
    mark_iv: float | None
    mark_iv_status: str
    deribit_mark_iv: float | None
    iv_diff_points: float | None  # our mark IV minus Deribit's, in vol points
    iv_spread_points: float | None  # ask IV minus bid IV: the executable range of fair vol
    delta: float | None
    gamma: float | None
    vega: float | None  # per vol point
    theta: float | None  # per calendar day
    flags: tuple[QualityFlag, ...] = ()  # arbitrage flags only; quote flags stay on the quote


class ChainPricing(FrozenModel):
    snapshot_at: UtcDateTime
    model_version: str
    inputs_hash: str
    valuations: tuple[ContractValuation, ...]  # same order as snapshot.rows


def _price(value: float | None) -> float:
    return math.nan if value is None or value <= 0 else value


def _opt(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def price_chain(snapshot: ChainSnapshot) -> ChainPricing:
    rows = snapshot.rows
    forward = np.array([r.quote.underlying_price or math.nan for r in rows], dtype=np.float64)
    strike = np.array([float(r.instrument.strike) for r in rows], dtype=np.float64)
    years = np.array(
        [time_to_expiry_years(snapshot.snapshot_at, r.instrument.expiration) for r in rows],
        dtype=np.float64,
    )
    is_call = np.array([r.instrument.option_type == "call" for r in rows], dtype=bool)
    rate = np.array([r.quote.interest_rate or 0.0 for r in rows], dtype=np.float64)
    bid = np.array([_price(r.quote.bid_price) for r in rows], dtype=np.float64)
    ask = np.array([_price(r.quote.ask_price) for r in rows], dtype=np.float64)
    mark = np.array([_price(r.quote.mark_price) for r in rows], dtype=np.float64)
    mid = np.where(np.isfinite(bid) & np.isfinite(ask), (bid + ask) / 2.0, math.nan)

    def solve(prices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        result = implied_vol(prices, forward, strike, years, is_call, rate)
        return np.where(result.ok, result.value, math.nan), result.status

    iv_bid, _ = solve(bid)
    iv_ask, _ = solve(ask)
    iv_mid, _ = solve(mid)
    iv_mark, mark_status = solve(mark)
    g = greeks(forward, strike, years, iv_mark, is_call, rate)
    violations = find_violations(rows)

    valuations = []
    for i, row in enumerate(rows):
        deribit_iv = row.quote.mark_iv
        ours = _opt(float(iv_mark[i]))
        diff = None if ours is None or deribit_iv is None else (ours - deribit_iv) * 100.0
        spread = _opt(float(iv_ask[i] - iv_bid[i]))
        fwd = _opt(float(forward[i]))
        valuations.append(
            ContractValuation(
                instrument_name=row.instrument.name,
                option_type=row.instrument.option_type,
                strike=float(strike[i]),
                expiration=row.instrument.expiration,
                years_to_expiry=float(years[i]),
                forward=fwd,
                log_moneyness=None if fwd is None else math.log(float(strike[i]) / fwd),
                bid_iv=_opt(float(iv_bid[i])),
                ask_iv=_opt(float(iv_ask[i])),
                mid_iv=_opt(float(iv_mid[i])),
                mark_iv=ours,
                mark_iv_status=_status_name(int(np.asarray(mark_status)[i])),
                deribit_mark_iv=deribit_iv,
                iv_diff_points=diff,
                iv_spread_points=None if spread is None else spread * 100.0,
                delta=_opt(float(np.asarray(g.delta)[i])),
                gamma=_opt(float(np.asarray(g.gamma)[i])),
                vega=_opt(float(np.asarray(g.vega)[i])),
                theta=_opt(float(np.asarray(g.theta)[i])),
                flags=tuple(
                    sorted(violations.get(row.instrument.name, ()), key=list(QualityFlag).index)
                ),
            )
        )
    return ChainPricing(
        snapshot_at=snapshot.snapshot_at,
        model_version=PRICING_MODEL_VERSION,
        inputs_hash=_inputs_hash(snapshot),
        valuations=tuple(valuations),
    )


def _status_name(code: int) -> str:
    return IVStatus(code).name


def _inputs_hash(snapshot: ChainSnapshot) -> str:
    payload = [
        snapshot.snapshot_at.isoformat(),
        PRICING_MODEL_VERSION,
        *(
            [
                r.instrument.name,
                r.quote.bid_price,
                r.quote.ask_price,
                r.quote.mark_price,
                r.quote.mark_iv,
                r.quote.underlying_price,
                r.quote.interest_rate,
            ]
            for r in snapshot.rows
        ),
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
