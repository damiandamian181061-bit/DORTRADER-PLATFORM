"""Pricing conventions in one place. Replicated against Deribit's own numbers on 2026-10-18.

Inputs
    F, K     USDC per 1 BTC. F is the expiry-matched future (Deribit's `underlying_price`),
             not the spot index.
    T        years, ACT/365, measured to the exchange expiry instant (08:00 UTC).
    sigma    decimal volatility (0.365 means 36.5%), never percent.
    r        continuously compounded rate; the discount factor is df = exp(-r * T).
             Deribit reports r = 0 for these options, and that is the default.

Outputs, per 1 BTC of contract size (scale to a position with `Greeks.scaled`)
    price    USDC
    delta    d price / d F (forward delta)
    gamma    d delta / d F, per USDC
    vega     per 1 volatility point (0.01 of sigma)
    theta    per calendar day, time decay at fixed F and sigma
    vanna    d delta / d (1 vol point)
    volga    d vega / d (1 vol point)
Rho is not offered: Deribit's rho did not match a plain Black-76 rho, and r = 0 here.
"""

from datetime import datetime

PRICING_MODEL_VERSION = "black76-1"
DAYS_PER_YEAR = 365.0
SECONDS_PER_YEAR = 365.0 * 86_400.0


def time_to_expiry_years(now: datetime, expiry: datetime) -> float:
    """ACT/365 years from `now` to `expiry`; zero once expired. Both must be timezone-aware."""
    if now.tzinfo is None or expiry.tzinfo is None:
        raise ValueError("datetimes must be timezone-aware")
    return max(0.0, (expiry - now).total_seconds()) / SECONDS_PER_YEAR
