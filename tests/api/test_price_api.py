from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from dortrader.pricing import greeks, price

BASE: dict[str, Any] = {
    "forward": 81_000.0,
    "strike": 80_000.0,
    "days_to_expiry": 30.0,
    "option_type": "put",
    "mode": "vol",
    "value": 40.0,
}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def calculate(client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = client.post("/api/price", json=BASE | overrides)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_a_vol_gives_the_black_76_price_and_greeks(client: TestClient) -> None:
    body = calculate(client)

    g = greeks(81_000.0, 80_000.0, 30 / 365, 0.40, False)
    contract = body["per_contract"]
    assert contract["price"] == pytest.approx(float(g.price))
    assert contract["delta"] == pytest.approx(float(g.delta))
    assert contract["vega"] == pytest.approx(float(g.vega))
    assert contract["theta"] == pytest.approx(float(g.theta))
    assert body["vol"] == 0.40 and body["vol_status"] == "given"
    assert body["intrinsic"] == 0.0 and body["time_value"] == pytest.approx(contract["price"])
    assert body["model_version"] == "black76-1" and body["years_to_expiry"] == pytest.approx(
        30 / 365
    )


def test_the_position_is_the_contract_scaled_by_quantity(client: TestClient) -> None:
    body = calculate(client, quantity=2.5)

    for field in ("price", "delta", "gamma", "vega", "theta", "vanna", "volga"):
        assert body["position"][field] == pytest.approx(2.5 * body["per_contract"][field])


def test_a_price_is_inverted_to_the_vol_that_produced_it(client: TestClient) -> None:
    premium = float(price(81_000.0, 80_000.0, 30 / 365, 0.365, True))

    body = calculate(client, option_type="call", mode="price", value=premium)

    assert body["vol_status"] == "OK" and body["vol"] == pytest.approx(0.365, abs=1e-9)
    assert body["per_contract"]["price"] == pytest.approx(premium)
    assert body["per_contract"]["delta"] > 0


def test_a_price_no_vol_can_explain_is_reported_without_greeks(client: TestClient) -> None:
    body = calculate(client, option_type="call", strike=70_000.0, mode="price", value=100.0)

    assert body["vol_status"] == "BELOW_INTRINSIC"
    assert body["vol"] is None and body["per_contract"] is None and body["position"] is None
    assert body["intrinsic"] == pytest.approx(11_000.0)


def test_a_tick_gives_an_iv_interval_around_the_solved_vol(client: TestClient) -> None:
    premium = float(price(81_000.0, 95_000.0, 2 / 365, 0.40, True))  # a few ticks above zero

    body = calculate(
        client,
        option_type="call",
        strike=95_000.0,
        days_to_expiry=2.0,
        mode="price",
        value=premium,
        tick=5.0,
    )

    low, high = body["iv_interval"]["low"], body["iv_interval"]["high"]
    assert low < body["vol"] < high
    assert (
        calculate(client, mode="price", value=500.0)["iv_interval"] is None
    )  # no tick, no interval


def test_deribit_style_theta_is_capped_at_the_option_value_inside_the_last_day(
    client: TestClient,
) -> None:
    body = calculate(
        client,
        option_type="call",
        forward=80_885.0,
        strike=83_000.0,
        days_to_expiry=0.52,
        value=35.0,
    )

    contract = body["per_contract"]
    assert abs(contract["theta"]) > contract["price"]  # plain theta would over-decay it
    assert body["theta_deribit_style"] == pytest.approx(-contract["price"])


def test_the_rate_enters_through_discounting_and_put_call_parity_holds(
    client: TestClient,
) -> None:
    call = calculate(client, option_type="call", rate=0.05)["per_contract"]["price"]
    put = calculate(client, option_type="put", rate=0.05)["per_contract"]["price"]
    no_rate = calculate(client, option_type="call")["per_contract"]["price"]

    discount = pytest.approx(0.0, abs=1e-6)
    import math

    assert call - put - math.exp(-0.05 * 30 / 365) * (81_000.0 - 80_000.0) == discount
    assert call != pytest.approx(no_rate)


@pytest.mark.parametrize(
    "bad",
    [
        {"forward": -1},
        {"strike": 0},
        {"days_to_expiry": 0},
        {"days_to_expiry": 99_999},
        {"value": 0},
        {"quantity": 0},
        {"mode": "guess"},
        {"option_type": "straddle"},
        {"tick": -5},
    ],
)
def test_invalid_input_is_rejected(client: TestClient, bad: dict[str, Any]) -> None:
    assert client.post("/api/price", json=BASE | bad).status_code == 422
