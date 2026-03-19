import pytest
import asyncio
import uuid
from unittest.mock import AsyncMock, patch
from unittest.mock import MagicMock
from main_platform import TradingSession
from structures import OrderStatus, OrderType, TraderType, Order


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
async def test_add_order_mbo_event_contains_queue_position_and_size():
    session = TradingSession(duration=1)
    session.active = True
    session.sql_enabled = True
    session.sql_store = MagicMock()

    existing_bid_id = uuid.uuid4()
    session.connected_traders = {
        "existing_bidder": {"trader_type": TraderType.HUMAN.value, "cash": 1000, "shares": 0},
        "new_bidder": {"trader_type": TraderType.HUMAN.value, "cash": 1000, "shares": 0},
    }
    session.all_orders = {
        existing_bid_id: {
            "id": existing_bid_id,
            "trader_id": "existing_bidder",
            "order_type": OrderType.BID.value,
            "price": 100,
            "amount": 1,
            "timestamp": "2023-04-01T00:00:01Z",
            "status": OrderStatus.ACTIVE.value,
        }
    }

    response = await session.handle_add_order(
        {
            "trader_id": "new_bidder",
            "order_type": OrderType.BID.value,
            "price": 100,
            "amount": 1,
        }
    )

    assert response.get("status") != "failed"
    assert session.sql_store.save_mbo_event.call_count >= 1
    payload = session.sql_store.save_mbo_event.call_args_list[0].args[1]
    assert payload["event_type"] == "add"
    assert payload["queue_position"] == 2
    assert payload["queue_size"] == 2


@pytest.mark.asyncio
async def test_add_order_mbo_event_excludes_removed_aggressiveness_metrics():
    session = TradingSession(duration=1)
    session.active = True
    session.sql_enabled = True
    session.sql_store = MagicMock()

    bid_id = uuid.uuid4()
    ask_id = uuid.uuid4()
    session.connected_traders = {
        "bidder_existing": {"trader_type": TraderType.HUMAN.value, "cash": 1000, "shares": 0},
        "asker_existing": {"trader_type": TraderType.HUMAN.value, "cash": 0, "shares": 10},
        "bidder_new": {"trader_type": TraderType.HUMAN.value, "cash": 1000, "shares": 0},
    }
    session.all_orders = {
        bid_id: {
            "id": bid_id,
            "trader_id": "bidder_existing",
            "order_type": OrderType.BID.value,
            "price": 90,
            "amount": 1,
            "timestamp": "2023-04-01T00:00:01Z",
            "status": OrderStatus.ACTIVE.value,
        },
        ask_id: {
            "id": ask_id,
            "trader_id": "asker_existing",
            "order_type": OrderType.ASK.value,
            "price": 110,
            "amount": 1,
            "timestamp": "2023-04-01T00:00:02Z",
            "status": OrderStatus.ACTIVE.value,
        },
    }

    response = await session.handle_add_order(
        {
            "trader_id": "bidder_new",
            "order_type": OrderType.BID.value,
            "price": 105,
            "amount": 1,
        }
    )

    assert response.get("status") != "failed"
    assert session.sql_store.save_mbo_event.call_count >= 1
    payload = session.sql_store.save_mbo_event.call_args_list[0].args[1]
    assert payload["event_type"] == "add"
    assert "submit_best_bid_px" not in payload
    assert "submit_best_ask_px" not in payload
    assert "submit_spread" not in payload
    assert "aggr_dist_to_opp_touch" not in payload
    assert "aggr_dist_to_opp_touch_over_spread" not in payload


@pytest.mark.asyncio
async def test_crossing_buy_executes_at_resting_ask_price_and_marks_buy_aggressor():
    session = TradingSession(duration=1)
    session.active = True
    ask_id = uuid.uuid4()
    session.connected_traders = {
        "buyer": {"trader_type": TraderType.HUMAN.value, "cash": 1000, "shares": 0},
        "seller": {"trader_type": TraderType.HUMAN.value, "cash": 0, "shares": 10},
    }
    session.all_orders = {
        ask_id: {
            "id": ask_id,
            "trader_id": "seller",
            "order_type": OrderType.ASK.value,
            "price": 15,
            "amount": 10,
            "timestamp": "2023-04-01T00:00:10Z",
            "status": OrderStatus.ACTIVE.value,
        },
    }

    response = await session.handle_add_order(
        {
            "trader_id": "buyer",
            "order_type": OrderType.BID.value,
            "price": 16,
            "amount": 2,
        }
    )
    assert response.get("status") != "failed"
    assert len(session._transactions_mem) == 1
    tx = session._transactions_mem[0]
    assert tx["price"] == 15
    assert tx["quantity"] == 2
    assert tx["aggressor_side"] == "B"


@pytest.mark.asyncio
async def test_crossing_trade_emits_single_post_trade_mbp_snapshot():
    session = TradingSession(duration=1)
    session.active = True
    session.sql_enabled = True
    session.sql_store = MagicMock()
    ask_id = uuid.uuid4()
    session.connected_traders = {
        "buyer": {"trader_type": TraderType.HUMAN.value, "cash": 1000, "shares": 0},
        "seller": {"trader_type": TraderType.HUMAN.value, "cash": 0, "shares": 10},
    }
    session.all_orders = {
        ask_id: {
            "id": ask_id,
            "trader_id": "seller",
            "order_type": OrderType.ASK.value,
            "price": 15,
            "amount": 10,
            "timestamp": "2023-04-01T00:00:10Z",
            "status": OrderStatus.ACTIVE.value,
        },
    }

    response = await session.handle_add_order(
        {
            "trader_id": "buyer",
            "order_type": OrderType.BID.value,
            "price": 16,
            "amount": 2,
        }
    )

    assert response.get("status") != "failed"
    assert session.sql_store.save_mbp1_event.call_count == 1
    mbp_payload = session.sql_store.save_mbp1_event.call_args.args[1]
    assert mbp_payload["source_record_kind"] == "trade"
    assert mbp_payload["source_event_type"] == "trade"


@pytest.mark.asyncio
async def test_self_cross_records_only_resting_cancel_and_final_mbp_snapshot():
    session = TradingSession(duration=1)
    session.active = True
    session.sql_enabled = True
    session.sql_store = MagicMock()

    bid_7_id = uuid.uuid4()
    ask_13_id = uuid.uuid4()
    ask_16_id = uuid.uuid4()

    session.connected_traders = {
        "self_trader": {"trader_type": TraderType.HUMAN.value, "cash": 1000, "shares": 10, "reserved_cash": 0},
        "bidder_other": {"trader_type": TraderType.HUMAN.value, "cash": 1000, "shares": 0},
        "asker_other": {"trader_type": TraderType.HUMAN.value, "cash": 0, "shares": 10},
    }
    session.all_orders = {
        bid_7_id: {
            "id": bid_7_id,
            "trader_id": "bidder_other",
            "order_type": OrderType.BID.value,
            "price": 7,
            "amount": 1,
            "timestamp": "2023-04-01T00:00:01Z",
            "status": OrderStatus.ACTIVE.value,
        },
        ask_13_id: {
            "id": ask_13_id,
            "trader_id": "self_trader",
            "order_type": OrderType.ASK.value,
            "price": 13,
            "amount": 1,
            "timestamp": "2023-04-01T00:00:02Z",
            "status": OrderStatus.ACTIVE.value,
        },
        ask_16_id: {
            "id": ask_16_id,
            "trader_id": "asker_other",
            "order_type": OrderType.ASK.value,
            "price": 16,
            "amount": 1,
            "timestamp": "2023-04-01T00:00:03Z",
            "status": OrderStatus.ACTIVE.value,
        },
    }

    response = await session.handle_add_order(
        {
            "trader_id": "self_trader",
            "order_type": OrderType.BID.value,
            "price": 15,
            "amount": 1,
        }
    )

    assert response.get("status") != "failed"

    assert session.sql_store.save_mbo_event.call_count == 1
    mbo_payload = session.sql_store.save_mbo_event.call_args.args[1]
    assert mbo_payload["event_type"] == "cancel"
    assert mbo_payload["side"] == "ask"
    assert str(mbo_payload["order_id"]) == str(ask_13_id)
    assert mbo_payload["price"] == 13

    assert session.sql_store.save_mbp1_event.call_count == 1
    mbp_payload = session.sql_store.save_mbp1_event.call_args.args[1]
    assert mbp_payload["source_record_kind"] == "order"
    assert mbp_payload["source_event_type"] == "cancel"
    assert str(mbp_payload["source_order_id"]) == str(ask_13_id)
    assert mbp_payload["source_mbo_event_seq"] == 1
    assert mbp_payload["best_bid_px"] == 7
    assert mbp_payload["best_bid_sz"] == 1
    assert mbp_payload["best_bid_ct"] == 1
    assert mbp_payload["best_ask_px"] == 16
    assert mbp_payload["best_ask_sz"] == 1
    assert mbp_payload["best_ask_ct"] == 1

    remaining_active = {
        str(order["id"]): order
        for order in session.all_orders.values()
        if order["status"] == OrderStatus.ACTIVE.value
    }
    assert set(remaining_active.keys()) == {str(bid_7_id), str(ask_16_id)}
    assert session.connected_traders["self_trader"]["reserved_cash"] == 0


@pytest.mark.asyncio
async def test_handle_gamification_event_persists_ui_event():
    session = TradingSession(duration=1)
    session.active = True
    session.sql_enabled = True
    session.sql_store = MagicMock()

    await session.handle_gamification_event(
        {
            "trader_id": "trader-1",
            "event_name": "shown",
            "element_type": "trend_alert",
            "element_id": "up-streak",
            "element_label": "up-streak",
            "client_ts": "2026-03-03T12:00:00Z",
            "message": "Price went up 3 times in the last 5 ticks!",
        }
    )

    assert session.sql_store.save_gamification_ui_event.call_count == 1
    call_kwargs = session.sql_store.save_gamification_ui_event.call_args.kwargs
    assert call_kwargs["trading_session_uuid"] == session.id
    assert call_kwargs["trader_uuid"] == "trader-1"
    assert call_kwargs["event_name"] == "shown"
    assert call_kwargs["element_type"] == "trend_alert"


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


@pytest.mark.asyncio
async def test_matching_respects_cash_and_never_goes_negative():
    session = TradingSession(duration=1)
    session.active = True
    ask_id = uuid.uuid4()
    session.connected_traders = {
        "buyer": {"trader_type": TraderType.HUMAN.value, "cash": 150},
        "seller": {"trader_type": TraderType.HUMAN.value, "cash": 0},
    }
    session.all_orders = {
        ask_id: {
            "id": ask_id,
            "trader_id": "seller",
            "order_type": OrderType.ASK.value,
            "price": 100,
            "amount": 3,
            "timestamp": "2023-04-01T00:00:10Z",
            "status": OrderStatus.ACTIVE.value,
        },
    }

    response = await session.handle_add_order(
        {
            "trader_id": "buyer",
            "order_type": OrderType.BID.value,
            "price": 100,
            "amount": 3,
            "immediate": True,
        }
    )

    assert response["status"] == "failed"
    assert "Insufficient available cash" in response["reason"]
    assert session.connected_traders["buyer"]["cash"] == 150
    assert session.connected_traders["buyer"]["cash"] >= 0
    assert session.all_orders[ask_id]["status"] == OrderStatus.ACTIVE.value
    assert session.all_orders[ask_id]["amount"] == 3


def test_alert_uses_rolling_window_not_consecutive_streak():
    session = TradingSession(duration=1, alert_streak_frequency=4)
    transactions = []
    prices = [100, 101, 100, 101, 102, 103]  # moves: up, down, up, up, up -> 4 ups in last 5

    last_alert = ""
    for idx, price in enumerate(prices):
        transactions.append({"price": price, "timestamp": idx})
        alert, _ = session._build_alert_message(transactions)
        if alert:
            last_alert = alert

    assert "went up 4 times in the last 5 ticks" in last_alert


def test_alert_triggers_once_per_continuous_up_run_and_rearms_after_reset():
    session = TradingSession(duration=1, alert_streak_frequency=3, alert_window_size=5)
    transactions = []
    prices = [
        100, 101, 102, 103, 102, 103, 104,  # first up run, should alert once
        103, 102,                            # break the up condition and reset the latch
        103, 104, 105,                       # second up run, should alert again
    ]

    alerts = []
    for idx, price in enumerate(prices):
        transactions.append({"price": price, "timestamp": idx})
        alert, _ = session._build_alert_message(transactions)
        if alert:
            alerts.append(alert)

    assert len(alerts) == 2
    assert all("Price went up" in alert for alert in alerts)


@pytest.mark.asyncio
async def test_noise_order_is_forced_to_unit_amount_and_integer_price():
    session = TradingSession(duration=1)
    session.active = True
    session.connected_traders = {
        "noise-1": {"trader_type": TraderType.NOISE.value, "cash": 1000, "shares": 100},
    }

    await session.handle_add_order(
        {
            "trader_id": "noise-1",
            "order_type": OrderType.BID.value,
            "price": 71.23,
            "amount": 5,
        }
    )

    noise_orders = [
        order
        for order in session.all_orders.values()
        if order["trader_id"] == "noise-1"
    ]
    assert len(noise_orders) == 1
    assert noise_orders[0]["amount"] == 1
    assert noise_orders[0]["price"] == 71


def test_noise_order_is_forced_in_place_order_as_last_safety_net():
    session = TradingSession(duration=1)
    session.connected_traders = {
        "noise-1": {"trader_type": TraderType.NOISE.value, "cash": 1000, "shares": 100},
    }

    order = Order(
        status=OrderStatus.BUFFERED.value,
        session_id=session.id,
        trader_id="noise-1",
        order_type=OrderType.BID.value,
        price=88.9,
        amount=7,
    ).model_dump()

    placed = session.place_order(order)
    assert placed["amount"] == 1
    assert placed["price"] == 89


@pytest.mark.asyncio
async def test_buy_order_reserves_cash_and_blocks_overcommit():
    session = TradingSession(duration=1)
    session.active = True
    session.connected_traders = {
        "buyer": {"trader_type": TraderType.HUMAN.value, "cash": 1000, "shares": 0, "reserved_cash": 0},
    }

    response_1 = await session.handle_add_order(
        {
            "trader_id": "buyer",
            "order_type": OrderType.BID.value,
            "price": 100,
            "amount": 7,
        }
    )
    assert response_1.get("status") != "failed"
    assert session.connected_traders["buyer"]["reserved_cash"] == 700

    response_2 = await session.handle_add_order(
        {
            "trader_id": "buyer",
            "order_type": OrderType.BID.value,
            "price": 100,
            "amount": 4,
        }
    )
    assert response_2["status"] == "failed"
    assert session.connected_traders["buyer"]["reserved_cash"] == 700


@pytest.mark.asyncio
async def test_cancel_buy_order_releases_reserved_cash():
    session = TradingSession(duration=1)
    session.active = True
    session.connected_traders = {
        "buyer": {"trader_type": TraderType.HUMAN.value, "cash": 1000, "shares": 0, "reserved_cash": 0},
    }

    response = await session.handle_add_order(
        {
            "trader_id": "buyer",
            "order_type": OrderType.BID.value,
            "price": 100,
            "amount": 5,
        }
    )
    assert response.get("status") != "failed"
    assert session.connected_traders["buyer"]["reserved_cash"] == 500

    bid_order = [
        order for order in session.all_orders.values()
        if order["trader_id"] == "buyer" and int(order["order_type"]) == int(OrderType.BID)
    ][0]

    cancel_response = await session.handle_cancel_order(
        {"order_id": str(bid_order["id"]), "trader_id": "buyer"}
    )
    assert cancel_response["status"] == "cancel success"
    assert session.connected_traders["buyer"]["reserved_cash"] == 0


@pytest.mark.asyncio
async def test_partial_fill_releases_reserved_cash_for_filled_quantity():
    session = TradingSession(duration=1)
    session.active = True
    ask_id = uuid.uuid4()
    session.connected_traders = {
        "buyer": {"trader_type": TraderType.HUMAN.value, "cash": 1000, "shares": 0, "reserved_cash": 0},
        "seller": {"trader_type": TraderType.HUMAN.value, "cash": 0, "shares": 10, "reserved_cash": 0},
    }
    session.all_orders = {
        ask_id: {
            "id": ask_id,
            "trader_id": "seller",
            "order_type": OrderType.ASK.value,
            "price": 100,
            "amount": 2,
            "timestamp": "2023-04-01T00:00:10Z",
            "status": OrderStatus.ACTIVE.value,
        },
    }

    response = await session.handle_add_order(
        {
            "trader_id": "buyer",
            "order_type": OrderType.BID.value,
            "price": 120,
            "amount": 5,
        }
    )
    assert response.get("status") != "failed"
    # Reserved at entry: 5 * 120 = 600; filled 2 shares -> release 2 * 120 = 240.
    assert session.connected_traders["buyer"]["reserved_cash"] == 360


@pytest.mark.asyncio
async def test_sell_order_reserves_shares_and_blocks_oversell():
    session = TradingSession(duration=1)
    session.active = True
    session.connected_traders = {
        "seller": {"trader_type": TraderType.HUMAN.value, "cash": 1000, "shares": 5, "reserved_shares": 0},
    }

    response_1 = await session.handle_add_order(
        {
            "trader_id": "seller",
            "order_type": OrderType.ASK.value,
            "price": 100,
            "amount": 4,
        }
    )
    assert response_1.get("status") != "failed"
    assert session.connected_traders["seller"]["reserved_shares"] == 4

    response_2 = await session.handle_add_order(
        {
            "trader_id": "seller",
            "order_type": OrderType.ASK.value,
            "price": 110,
            "amount": 2,
        }
    )
    assert response_2["status"] == "failed"
    assert "Insufficient available shares" in response_2["reason"]
    assert session.connected_traders["seller"]["reserved_shares"] == 4


@pytest.mark.asyncio
async def test_cancel_sell_order_releases_reserved_shares():
    session = TradingSession(duration=1)
    session.active = True
    session.connected_traders = {
        "seller": {"trader_type": TraderType.HUMAN.value, "cash": 1000, "shares": 5, "reserved_shares": 0},
    }

    response = await session.handle_add_order(
        {
            "trader_id": "seller",
            "order_type": OrderType.ASK.value,
            "price": 100,
            "amount": 4,
        }
    )
    assert response.get("status") != "failed"
    assert session.connected_traders["seller"]["reserved_shares"] == 4

    ask_order = [
        order for order in session.all_orders.values()
        if order["trader_id"] == "seller" and int(order["order_type"]) == int(OrderType.ASK)
    ][0]

    cancel_response = await session.handle_cancel_order(
        {"order_id": str(ask_order["id"]), "trader_id": "seller"}
    )
    assert cancel_response["status"] == "cancel success"
    assert session.connected_traders["seller"]["reserved_shares"] == 0


@pytest.mark.asyncio
async def test_partial_fill_releases_reserved_shares_for_filled_quantity():
    session = TradingSession(duration=1)
    session.active = True
    bid_id = uuid.uuid4()
    session.connected_traders = {
        "seller": {"trader_type": TraderType.HUMAN.value, "cash": 0, "shares": 5, "reserved_shares": 0},
        "buyer": {"trader_type": TraderType.HUMAN.value, "cash": 1000, "shares": 0, "reserved_cash": 0},
    }
    session.all_orders = {
        bid_id: {
            "id": bid_id,
            "trader_id": "buyer",
            "order_type": OrderType.BID.value,
            "price": 120,
            "amount": 2,
            "timestamp": "2023-04-01T00:00:10Z",
            "status": OrderStatus.ACTIVE.value,
        },
    }

    response = await session.handle_add_order(
        {
            "trader_id": "seller",
            "order_type": OrderType.ASK.value,
            "price": 100,
            "amount": 5,
        }
    )
    assert response.get("status") != "failed"
    # Reserved at entry: 5; filled 2 shares -> release 2.
    assert session.connected_traders["seller"]["reserved_shares"] == 3
