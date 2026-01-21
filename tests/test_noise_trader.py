import pytest
from unittest.mock import AsyncMock, patch
from traders import NoiseTrader
from structures import TraderType, OrderType

@pytest.fixture
def noise_trader_settings():
    return {
        "activity_frequency": 1.0,
        "order_amount": 1,
        "settings": {
            "initial_price": 100,
            "price_step": 1,
            "cancel_probability": 0.25,
            "max_price_deviation": 5,
        },
    }

@pytest.fixture
def noise_trader(noise_trader_settings):
    return NoiseTrader(**noise_trader_settings)


def test_initialization(noise_trader):
    assert noise_trader.activity_frequency == 1.0
    assert noise_trader.initial_price == 100
    assert noise_trader.price_step == 1
    assert noise_trader.trader_type == TraderType.NOISE


def test_cooling_interval(noise_trader):
    target = 5.0
    interval = noise_trader.cooling_interval(target)
    assert interval >= 0, "Interval should be non-negative"


@pytest.mark.asyncio
async def test_act_with_no_active_orders(noise_trader):
    noise_trader.active_orders = []
    noise_trader.post_new_order = AsyncMock()
    await noise_trader.act()
    noise_trader.post_new_order.assert_awaited_once()

@pytest.mark.asyncio
async def test_act_with_cancel_order(noise_trader):
    noise_trader.active_orders = [{"order_type": OrderType.BID}]
    noise_trader.orders = [{"id": "1", "order_type": "ask"}]
    noise_trader.cancel_probability = 1.0
    noise_trader.send_cancel_order_request = AsyncMock()
    with patch("random.random", return_value=0.0):
        await noise_trader.act()
    noise_trader.send_cancel_order_request.assert_awaited_once_with("1")


@pytest.mark.asyncio
async def test_act_with_add_order(noise_trader):
    noise_trader.active_orders = [{"order_type": OrderType.BID}]
    noise_trader.orders = []
    noise_trader.cancel_probability = 0.0
    noise_trader.post_new_order = AsyncMock()
    await noise_trader.act()
    noise_trader.post_new_order.assert_awaited_once()
