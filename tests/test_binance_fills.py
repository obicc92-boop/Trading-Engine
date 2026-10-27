"""Turning a Binance order response into the fill the engine books.

No network: these feed `_fill_from` the response shapes Binance returns. The property that
matters is that the recorded quantity is what the account actually holds afterwards, and
the fee is in the quote currency whatever it was paid in.
"""

import pytest

from helm.config import Config
from helm.venues.binance import BNB_TAKER_FEE, BinanceVenue


@pytest.fixture
def venue():
    config = Config()
    config.quote = "EUR"
    return BinanceVenue(config)


def _response(side, qty, price, commission, asset):
    return {
        "orderId": 1, "status": "FILLED", "side": side,
        "executedQty": f"{qty}", "cummulativeQuoteQty": f"{qty * price}",
        "fills": [{"price": f"{price}", "qty": f"{qty}",
                   "commission": f"{commission}", "commissionAsset": asset}],
    }


def test_a_buy_paid_for_in_the_coin_records_what_was_received(venue):
    """With no BNB, the fee comes out of the BTC bought. Recording the gross quantity made
    every final exit ask for coins the account did not have."""
    fill = venue._fill_from(_response("BUY", 0.00052, 70_000.0, 0.00000052, "BTC"), "BTCEUR", "BUY")
    assert fill.qty == pytest.approx(0.00052 - 0.00000052)
    assert fill.fee == pytest.approx(0.00000052 * 70_000.0)
    assert fill.price == pytest.approx(70_000.0)


def test_a_fee_in_the_quote_currency_leaves_the_quantity_alone(venue):
    fill = venue._fill_from(_response("BUY", 0.00052, 70_000.0, 0.0364, "EUR"), "BTCEUR", "BUY")
    assert fill.qty == pytest.approx(0.00052)
    assert fill.fee == pytest.approx(0.0364)


def test_a_sell_books_its_quote_fee(venue):
    fill = venue._fill_from(_response("SELL", 0.00052, 71_000.0, 0.0369, "EUR"), "BTCEUR", "SELL")
    assert fill.qty == pytest.approx(0.00052)
    assert fill.fee == pytest.approx(0.0369)


def test_a_bnb_fee_is_valued_not_counted_as_cash(venue):
    """0.00005 BNB is not 0.00005 euros. It used to be added to the fee as if it were."""
    fill = venue._fill_from(_response("BUY", 0.00052, 70_000.0, 0.00005, "BNB"), "BTCEUR", "BUY")
    assert fill.qty == pytest.approx(0.00052)
    assert fill.fee == pytest.approx(0.00052 * 70_000.0 * BNB_TAKER_FEE)


def test_fees_across_several_legs_add_up(venue):
    response = {
        "orderId": 2, "status": "FILLED", "executedQty": "0.002",
        "cummulativeQuoteQty": f"{0.001 * 70_000 + 0.001 * 70_100}",
        "fills": [
            {"price": "70000", "qty": "0.001", "commission": "0.000001", "commissionAsset": "BTC"},
            {"price": "70100", "qty": "0.001", "commission": "0.000001", "commissionAsset": "BTC"},
        ],
    }
    fill = venue._fill_from(response, "BTCEUR", "BUY")
    assert fill.qty == pytest.approx(0.002 - 0.000002)
    assert fill.fee == pytest.approx(0.000001 * 70_000 + 0.000001 * 70_100)
    assert fill.price == pytest.approx(70_050.0)
