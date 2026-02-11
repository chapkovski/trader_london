import pytest
import asyncio
import uuid
from unittest.mock import AsyncMock, patch
from main_platform import TradingSession
from structures import OrderStatus, OrderType, TraderType


@pytest.mark.asyncio
async def test_initialize():
    session = TradingSession(duration=1)
    session.connection = AsyncMock()
    session.channel = AsyncMock()
    with patch("aio_pika.connect_robust", return_value=session.connection), patch(
        "main_platform.trading_platform.now", return_value="2023-04-01T00:00:00Z"
    ):
        await session.initialize()
        session.connection.channel.assert_awaited()
        session.channel.declare_exchange.assert_awaited()
        assert session.active is True
        assert session.start_time == "2023-04-01T00:00:00Z"


@pytest.mark.asyncio
async def test_place_order():
    session = TradingSession(duration=1)
    order_dict = {
        "id": "test_order_id",
        "order_type": OrderType.BID.value,
        "amount": 100,
        "price": 1000,
        "status": OrderStatus.BUFFERED.value,
    }
    placed_order = session.place_order(order_dict)
    assert placed_order["status"] == OrderStatus.ACTIVE.value
    assert session.all_orders["test_order_id"] == placed_order


@pytest.mark.asyncio
async def test_order_book_empty():
    session = TradingSession(duration=1)
    order_book = session.order_book
    assert order_book == {
        "bids": [],
        "asks": [],
    }, "Order book should be empty initially"


@pytest.mark.asyncio
async def test_order_book_with_only_bids():
    session = TradingSession(duration=1)
    session.all_orders = {
        "bid_order_1": {
            "id": "bid_order_1",
            "order_type": OrderType.BID.value,
            "price": 1000,
            "amount": 10,
            "status": OrderStatus.ACTIVE.value,
        },
        "bid_order_2": {
            "id": "bid_order_2",
            "order_type": OrderType.BID.value,
            "price": 1010,
            "amount": 5,
            "status": OrderStatus.ACTIVE.value,
        },
    }
    order_book = session.order_book
    assert order_book["asks"] == [], "Expect no asks in the order book"
    assert len(order_book["bids"]) == 2, "Expect two bids in the order book"
    assert (
        order_book["bids"][0]["x"] == 1010
    ), "The first bid should have the highest price"


@pytest.mark.asyncio
async def test_order_book_with_only_asks():
    session = TradingSession(duration=1)
    session.all_orders = {
        "ask_order_1": {
            "id": "ask_order_1",
            "order_type": OrderType.ASK.value,
            "price": 1020,
            "amount": 10,
            "status": OrderStatus.ACTIVE.value,
        },
        "ask_order_2": {
            "id": "ask_order_2",
            "order_type": OrderType.ASK.value,
            "price": 1005,
            "amount": 5,
            "status": OrderStatus.ACTIVE.value,
        },
    }
    order_book = session.order_book
    assert order_book["bids"] == [], "Expect no bids in the order book"
    assert len(order_book["asks"]) == 2, "Expect two asks in the order book"
    assert (
        order_book["asks"][0]["x"] == 1005
    ), "The first ask should have the lowest price"


@pytest.mark.asyncio
async def test_order_book_with_bids_and_asks():
    session = TradingSession(duration=1)
    session.all_orders = {
        "bid_order": {
            "id": "bid_order",
            "order_type": OrderType.BID.value,
            "price": 1000,
            "amount": 10,
            "status": OrderStatus.ACTIVE.value,
        },
        "ask_order": {
            "id": "ask_order",
            "order_type": OrderType.ASK.value,
            "price": 1020,
            "amount": 5,
            "status": OrderStatus.ACTIVE.value,
        },
    }
    order_book = session.order_book
    assert len(order_book["bids"]) == 1, "Expect one bid in the order book"
    assert len(order_book["asks"]) == 1, "Expect one ask in the order book"
    assert order_book["bids"][0]["x"] == 1000, "The bid price should match"
    assert order_book["asks"][0]["x"] == 1020, "The ask price should match"


@pytest.mark.asyncio
async def test_get_spread():
    session = TradingSession(duration=1)
    session.all_orders = {
        "ask_order": {
            "id": "ask_order",
            "order_type": OrderType.ASK.value,
            "price": 1010,
            "timestamp": "2023-04-01T00:00:10Z",
            "status": OrderStatus.ACTIVE.value,
        },
        "bid_order": {
            "id": "bid_order",
            "order_type": OrderType.BID.value,
            "price": 1000,
            "timestamp": "2023-04-01T00:00:05Z",
            "status": OrderStatus.ACTIVE.value,
        },
    }
    spread = session.get_spread()
    assert spread[0] == 10


@pytest.mark.asyncio
async def test_clean_up():
    session = TradingSession(duration=1)
    session.connection = AsyncMock()
    session.channel = AsyncMock()
    session._stop_requested = asyncio.Event()
    session._stop_requested.set()
    await session.clean_up()
    session.channel.close.assert_awaited()
    session.connection.close.assert_awaited()
    assert session.active is False


@pytest.mark.asyncio
async def test_clear_orders_partial_fill_updates_remaining_amount():
    session = TradingSession(duration=1)
    ask_order_id = uuid.uuid4()
    bid_order_id = uuid.uuid4()
    session.connected_traders = {
        "ask_trader": {"trader_type": TraderType.HUMAN.value},
        "bid_trader": {"trader_type": TraderType.HUMAN.value},
    }
    session.all_orders = {
        ask_order_id: {
            "id": ask_order_id,
            "trader_id": "ask_trader",
            "order_type": OrderType.ASK.value,
            "price": 1000,
            "amount": 1,
            "timestamp": "2023-04-01T00:00:10Z",
            "status": OrderStatus.ACTIVE.value,
        },
        bid_order_id: {
            "id": bid_order_id,
            "trader_id": "bid_trader",
            "order_type": OrderType.BID.value,
            "price": 1100,
            "amount": 3,
            "timestamp": "2023-04-01T00:00:05Z",
            "status": OrderStatus.ACTIVE.value,
        },
    }

    result = await session.clear_orders()

    assert session.all_orders[ask_order_id]["status"] == OrderStatus.EXECUTED.value
    assert session.all_orders[ask_order_id]["amount"] == 0
    assert session.all_orders[bid_order_id]["status"] == OrderStatus.ACTIVE.value
    assert session.all_orders[bid_order_id]["amount"] == 2
    assert "subgroup_broadcast" in result
    assert result["subgroup_broadcast"]["ask_trader"][0]["amount"] == 1
    assert result["subgroup_broadcast"]["bid_trader"][0]["amount"] == 1


@pytest.mark.asyncio
async def test_immediate_buy_ioc_cascades_and_cancels_remainder():
    session = TradingSession(duration=1)
    session.active = True
    ask1_id = uuid.uuid4()
    ask2_id = uuid.uuid4()
    session.connected_traders = {
        "buyer": {"trader_type": TraderType.HUMAN.value},
        "seller_1": {"trader_type": TraderType.HUMAN.value},
        "seller_2": {"trader_type": TraderType.HUMAN.value},
    }
    session.all_orders = {
        ask1_id: {
            "id": ask1_id,
            "trader_id": "seller_1",
            "order_type": OrderType.ASK.value,
            "price": 1000,
            "amount": 1,
            "timestamp": "2023-04-01T00:00:10Z",
            "status": OrderStatus.ACTIVE.value,
        },
        ask2_id: {
            "id": ask2_id,
            "trader_id": "seller_2",
            "order_type": OrderType.ASK.value,
            "price": 1200,
            "amount": 2,
            "timestamp": "2023-04-01T00:00:11Z",
            "status": OrderStatus.ACTIVE.value,
        },
    }

    await session.handle_add_order(
        {
            "trader_id": "buyer",
            "order_type": OrderType.BID.value,
            "price": 1000,
            "amount": 5,
            "immediate": True,
        }
    )

    assert session.all_orders[ask1_id]["status"] == OrderStatus.EXECUTED.value
    assert session.all_orders[ask2_id]["status"] == OrderStatus.EXECUTED.value
    buyer_orders = [
        order
        for order in session.all_orders.values()
        if order["trader_id"] == "buyer" and order["order_type"] == OrderType.BID.value
    ]
    assert len(buyer_orders) == 1
    assert buyer_orders[0]["status"] == OrderStatus.CANCELLED.value
    assert buyer_orders[0]["amount"] == 2
    assert buyer_orders[0]["price"] == 1200
