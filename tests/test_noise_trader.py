import pytest
from unittest.mock import AsyncMock, patch
from traders import NoiseTrader
from structures import TraderType, OrderType

@pytest.fixture
def noise_trader():
    return NoiseTrader(max_orders_per_minute=3)


def test_initialization(noise_trader):
    assert noise_trader.max_orders_per_minute == 3
    assert noise_trader.trader_type == TraderType.NOISE


def test_orders_for_next_minute_is_capped(noise_trader):
    with patch.object(noise_trader, "_sample_poisson", return_value=7):
        assert noise_trader.orders_for_next_minute() == 3
    with patch.object(noise_trader, "_sample_poisson", return_value=2):
        assert noise_trader.orders_for_next_minute() == 2


@pytest.mark.asyncio
async def test_act_requires_human_bid_and_ask(noise_trader):
    noise_trader.active_orders = [
        {"trader_id": "human-1", "order_type": OrderType.BID, "price": 1000},
        {"trader_id": noise_trader.id, "order_type": OrderType.ASK, "price": 1100},
    ]
    noise_trader.post_new_order = AsyncMock()
    await noise_trader.act()
    noise_trader.post_new_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_act_market_buy(noise_trader):
    noise_trader.active_orders = [
        {"trader_id": "human-bid", "order_type": OrderType.BID, "price": 1000},
        {"trader_id": "human-ask", "order_type": OrderType.ASK, "price": 1100},
    ]
    noise_trader.post_new_order = AsyncMock()
    with patch("random.choice", return_value="market_buy"):
        await noise_trader.act()
    noise_trader.post_new_order.assert_awaited_once_with(1, 1100, OrderType.BID)


@pytest.mark.asyncio
async def test_act_limit_sell_uses_double_spread_distance(noise_trader):
    noise_trader.active_orders = [
        {"trader_id": "human-bid", "order_type": OrderType.BID, "price": 1000},
        {"trader_id": "human-ask", "order_type": OrderType.ASK, "price": 1100},
    ]
    noise_trader.post_new_order = AsyncMock()
    with patch("random.choice", return_value="limit_sell"):
        await noise_trader.act()
    noise_trader.post_new_order.assert_awaited_once_with(1, 1300, OrderType.ASK)
