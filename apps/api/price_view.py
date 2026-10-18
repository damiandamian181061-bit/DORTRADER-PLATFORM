"""The standalone Black-76 calculator: price and Greeks from a vol, or implied vol from a price."""

import math
from typing import Literal

from pydantic import BaseModel, Field

from dortrader.pricing import (
    PRICING_MODEL_VERSION,
    IVStatus,
    greeks,
    implied_vol_interval,
    implied_vol_scalar,
    theta_deribit_style,
)
from dortrader.pricing.black76 import Greeks


class PriceRequest(BaseModel):
    forward: float = Field(gt=0, le=1e7, description="USDC per BTC; the expiry-matched future")
    strike: float = Field(gt=0, le=1e7)
    days_to_expiry: float = Field(gt=0, le=3650, description="ACT/365 days to the expiry instant")
    option_type: Literal["call", "put"]
    rate: float = Field(default=0.0, ge=-1, le=1, description="continuously compounded, decimal")
    mode: Literal["vol", "price"] = Field(
        description="`vol`: value is a volatility in percent. `price`: value is a premium in USDC"
    )
    value: float = Field(gt=0, le=1e7)
    quantity: float = Field(default=1.0, gt=0, le=1e6, description="BTC; scales the position")
    tick: float | None = Field(default=None, gt=0, description="price tick, for the IV interval")


class GreeksView(BaseModel):
    price: float
    delta: float
    gamma: float
    vega: float  # per vol point
    theta: float  # per calendar day
    vanna: float
    volga: float


class IVInterval(BaseModel):
    low: float | None  # decimal; None when price minus half a tick is not solvable
    high: float | None  # None means unbounded above


class PriceResponse(BaseModel):
    model_version: str
    years_to_expiry: float
    vol: float | None  # decimal annualized: the input, or the solved implied vol
    vol_status: str  # "given" or the solver's status
    intrinsic: float
    time_value: float | None
    per_contract: GreeksView | None  # per 1 BTC of contract size
    position: GreeksView | None  # scaled by quantity
    theta_deribit_style: float | None  # theta as Deribit reports it near expiry
    iv_interval: IVInterval | None


def _finite(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def _view(g: Greeks) -> GreeksView:
    return GreeksView(
        price=float(g.price),
        delta=float(g.delta),
        gamma=float(g.gamma),
        vega=float(g.vega),
        theta=float(g.theta),
        vanna=float(g.vanna),
        volga=float(g.volga),
    )


def calculate(request: PriceRequest) -> PriceResponse:
    years = request.days_to_expiry / 365.0
    is_call = request.option_type == "call"
    f, k, r = request.forward, request.strike, request.rate
    intrinsic = math.exp(-r * years) * max((f - k) if is_call else (k - f), 0.0)
    interval = None
    if request.mode == "vol":
        vol: float | None = request.value / 100.0
        status = "given"
    else:
        solved, solver_status = implied_vol_scalar(request.value, f, k, years, is_call, r)
        vol = solved if solver_status is IVStatus.OK else None
        status = solver_status.name
        if request.tick is not None:
            band = implied_vol_interval(request.value, request.tick / 2, f, k, years, is_call, r)
            interval = IVInterval(low=_finite(float(band.low)), high=_finite(float(band.high)))
    if vol is None:
        return PriceResponse(
            model_version=PRICING_MODEL_VERSION,
            years_to_expiry=years,
            vol=None,
            vol_status=status,
            intrinsic=intrinsic,
            time_value=None,
            per_contract=None,
            position=None,
            theta_deribit_style=None,
            iv_interval=interval,
        )
    g = greeks(f, k, years, vol, is_call, r)
    price = float(g.price)
    return PriceResponse(
        model_version=PRICING_MODEL_VERSION,
        years_to_expiry=years,
        vol=vol,
        vol_status=status,
        intrinsic=intrinsic,
        time_value=price - intrinsic,
        per_contract=_view(g),
        position=_view(g.scaled(request.quantity)),
        theta_deribit_style=_finite(float(theta_deribit_style(g.theta, price, years))),
        iv_interval=interval,
    )
