from dortrader.domain import us_to_utc
from dortrader.market_data.normalize import parse_instruments
from dortrader.storage.tables import instruments_to_table, table_to_instruments
from tests.conftest import load_json


def test_instruments_survive_a_round_trip_through_arrow() -> None:
    raw = load_json("deribit", "instruments_usdc_option.json")
    original = parse_instruments(raw["result"], underlying="BTC", settlement_currency="USDC").items

    table = instruments_to_table(us_to_utc(raw["usOut"]), original)
    back = table_to_instruments(table)

    assert back == original
    assert back[0].tick_size_steps and back[0].tick_for(back[0].tick_size_steps[0].above_price * 2)
