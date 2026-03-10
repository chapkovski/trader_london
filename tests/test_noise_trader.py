import pytest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from traders import NoiseTrader
from structures import TraderType, OrderType

@pytest.fixture
def noise_trader():
    return NoiseTrader(max_orders_per_minute=3)


def test_initialization(noise_trader):
    assert noise_trader.max_orders_per_minute == 3
    assert noise_trader.initial_midpoint == 100
    assert noise_trader.initial_spread == 10
    assert noise_trader.start_second == 5
    assert noise_trader.trader_type == TraderType.NOISE


@pytest.mark.asyncio
async def test_seed_initial_orders(noise_trader):
    noise_trader.post_new_order = AsyncMock()
    await noise_trader.seed_initial_orders()
    noise_trader.post_new_order.assert_any_await(1, 95, OrderType.BID)
    noise_trader.post_new_order.assert_any_await(1, 105, OrderType.ASK)


def test_orders_for_next_minute_is_capped(noise_trader):
    with patch.object(noise_trader, "_sample_poisson", return_value=7):
        assert noise_trader.orders_for_next_minute() == 3
    with patch.object(noise_trader, "_sample_poisson", return_value=2):
        assert noise_trader.orders_for_next_minute() == 2


@pytest.mark.asyncio
async def test_act_requires_defined_spread(noise_trader):
    noise_trader.order_book = {
        "bids": [{"x": 1000, "y": 1}],
        "asks": [{"x": 1100, "y": 1}],
    }
    noise_trader.post_new_order = AsyncMock()
    with patch("random.choice", return_value="market_buy"):
        await noise_trader.act()
    noise_trader.post_new_order.assert_awaited_once_with(1, 1100, OrderType.BID)


@pytest.mark.asyncio
async def test_act_restores_missing_ask_when_only_bid_exists(noise_trader):
    noise_trader.order_book = {
        "bids": [{"x": 1000, "y": 1}],
        "asks": [],
    }
    noise_trader.post_new_order = AsyncMock()
    await noise_trader.act()
    noise_trader.post_new_order.assert_awaited_once_with(1, 1010, OrderType.ASK)


@pytest.mark.asyncio
async def test_act_places_both_sides_when_book_empty_using_last_price(noise_trader):
    noise_trader.order_book = {"bids": [], "asks": []}
    noise_trader.last_transaction_price = 200
    noise_trader.post_new_order = AsyncMock()

    await noise_trader.act()

    noise_trader.post_new_order.assert_any_await(1, 195, OrderType.BID)
    noise_trader.post_new_order.assert_any_await(1, 205, OrderType.ASK)


@pytest.mark.asyncio
async def test_act_market_buy(noise_trader):
    noise_trader.order_book = {
        "bids": [{"x": 1000, "y": 1}],
        "asks": [{"x": 1100, "y": 1}],
    }
    noise_trader.post_new_order = AsyncMock()
    with patch("random.choice", return_value="market_buy"):
        await noise_trader.act()
    noise_trader.post_new_order.assert_awaited_once_with(1, 1100, OrderType.BID)


@pytest.mark.asyncio
async def test_act_limit_sell_uses_random_offset_within_half_spread(noise_trader):
    noise_trader.order_book = {
        "bids": [{"x": 1000, "y": 1}],
        "asks": [{"x": 1100, "y": 1}],
    }
    noise_trader.post_new_order = AsyncMock()
    with patch("random.choice", return_value="limit_sell"), patch("random.uniform", return_value=17.0):
        await noise_trader.act()
    noise_trader.post_new_order.assert_awaited_once_with(1, 1117, OrderType.ASK)


@pytest.mark.asyncio
async def test_act_limit_buy_uses_random_offset_within_half_spread(noise_trader):
    noise_trader.order_book = {
        "bids": [{"x": 1000, "y": 1}],
        "asks": [{"x": 1100, "y": 1}],
    }
    noise_trader.post_new_order = AsyncMock()
    with patch("random.choice", return_value="limit_buy"), patch("random.uniform", return_value=17.0):
        await noise_trader.act()
    noise_trader.post_new_order.assert_awaited_once_with(1, 983, OrderType.BID)


def test_window_seconds_remaining_uses_current_day_progress(noise_trader):
    trading_session = SimpleNamespace(
        day_duration_minutes=1,
        seconds_into_current_day=lambda: 17.0,
    )
    assert noise_trader._window_seconds_remaining(trading_session) == 43.0


def test_orders_for_next_window_scales_with_remaining_seconds(noise_trader):
    with patch.object(noise_trader, "_sample_poisson", return_value=5) as sample:
        assert noise_trader.orders_for_next_window(30) == 3
        sample.assert_called_once_with(1.5)

    with patch.object(noise_trader, "_sample_poisson", return_value=2) as sample:
        assert noise_trader.orders_for_next_window(10) == 2
        sample.assert_called_once_with(0.5)
