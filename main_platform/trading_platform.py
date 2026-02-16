import aio_pika
import json
import uuid
from pydantic import ValidationError
from main_platform.custom_logger import setup_custom_logger
from typing import List, Dict
from structures import OrderStatus, OrderType, Order, TraderType
import asyncio
import os
from main_platform.utils import CustomEncoder, now, if_active
from asyncio import Lock
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import random
import math
from main_platform.sql_store import SQLStore

logger = setup_custom_logger(__name__)

SQL_DB = SQLStore(logger)
SQL_DB.initialize()
SQL_ENABLED = SQL_DB.enabled

rabbitmq_url = os.getenv('RABBITMQ_URL', 'amqp://localhost')


class TradingSession:
    duration: int
    active: bool
    start_time: datetime
    transactions = List[Dict]
    all_orders = Dict[uuid.UUID, Dict]

    def __init__(
        self,
        duration,
        default_price=1000,
        alert_streak_frequency=4,
        alert_window_size=5,
        allow_self_trade=False,
    ):
        self.active = False
        self.sql_enabled = SQL_ENABLED
        self.sql_store = SQL_DB
        self.duration = duration
        self.default_price = default_price
        self.alert_streak_frequency = max(1, int(alert_streak_frequency or 1))
        self.alert_window_size = max(1, int(alert_window_size or 5))
        self.allow_self_trade = bool(allow_self_trade)

        self._stop_requested = asyncio.Event()

        self.id = str(uuid.uuid4())

        self.creation_time = now()
        self.all_orders = {}

        self.broadcast_exchange_name = f'broadcast_{self.id}'
        self.queue_name = f'trading_system_queue_{self.id}'
        self.trader_exchange = None

        self.connected_traders = {}
        self.trader_transaction_counts = defaultdict(int)
        self.lock = Lock()
        self.current_price = 0  # handling non-defined attribute
        self._transactions_mem = []  # in-memory transaction history for runtime broadcasts
        self._last_broadcast_price = None
        self._last_seen_price = None
        self._last_seen_transaction_key = None
        self._alert_window_size = self.alert_window_size
        self._recent_price_moves = []
        self._last_alert_signature = None
        self._dividend_announced = False
        self._last_broadcast_book_levels = {"bids": {}, "asks": {}}
        self._last_trader_book_levels = {}
        self._last_trader_order_maps = {}
        self._last_trader_balances = {}

    @staticmethod
    def _transaction_ts(transaction):
        ts = transaction.get('timestamp')
        if hasattr(ts, "timestamp"):
            return ts.timestamp()
        return ts or 0

    def _latest_transaction_price(self, transactions):
        if not transactions:
            return None
        latest = max(transactions, key=self._transaction_ts)
        return latest.get('price')

    def _build_alert_message(self, transactions):
        if not transactions:
            return "", None
        ordered = sorted(transactions, key=self._transaction_ts)
        latest_price = ordered[-1].get('price')
        if latest_price is None:
            return "", None
        latest = ordered[-1]
        latest_key = (self._transaction_ts(latest), latest_price)
        if self._last_seen_transaction_key == latest_key:
            return "", latest_price
        self._last_seen_transaction_key = latest_key

        prev_price = self._last_seen_price
        self._last_seen_price = latest_price
        if prev_price is None:
            return "", latest_price

        if latest_price > prev_price:
            move = "up"
        elif latest_price < prev_price:
            move = "down"
        else:
            move = "flat"

        self._recent_price_moves.append(move)
        if len(self._recent_price_moves) > self._alert_window_size:
            self._recent_price_moves = self._recent_price_moves[-self._alert_window_size:]

        up_count = sum(1 for item in self._recent_price_moves if item == "up")
        down_count = sum(1 for item in self._recent_price_moves if item == "down")
        window_size = len(self._recent_price_moves)
        threshold = self.alert_streak_frequency

        candidate = None
        if up_count >= threshold and up_count > down_count:
            candidate = ("up", up_count, window_size)
        elif down_count >= threshold and down_count > up_count:
            candidate = ("down", down_count, window_size)

        if candidate and candidate != self._last_alert_signature:
            self._last_alert_signature = candidate
            direction, count, size = candidate
            return (f"Price went {direction} {count} times in the last {size} ticks!", latest_price)

        if not candidate:
            self._last_alert_signature = None
        return "", latest_price

    @property
    def current_time(self):
        return datetime.now(timezone.utc)

    @property
    def transactions(self):
        return self._transactions_mem

    @property
    def mid_price(self) -> float:
        return self.current_price or self.default_price


    def get_params(self):
        return {
            "id": self.id,
            "duration": self.duration,
            "creation_time": self.creation_time,
            "active": self.active,
            "start_time": self.start_time,
            "end_time": self.start_time + timedelta(minutes=self.duration),
            "connected_traders": self.connected_traders,
        }

    @property
    def active_orders(self):
        return {k: v for k, v in self.all_orders.items() if v['status'] == OrderStatus.ACTIVE}

    @property
    def order_book(self):
        # Initialize empty order book
        order_book = {'bids': [], 'asks': []}
        if not self.active_orders:
            return order_book

        active_bids = [order for order in self.active_orders.values() if order['order_type'] == OrderType.BID]
        active_asks = [order for order in self.active_orders.values() if order['order_type'] == OrderType.ASK]
        if active_bids:
            order_book['bids'] = self._aggregate_orders_by_price(active_bids, reverse=True)
        if active_asks:
            order_book['asks'] = self._aggregate_orders_by_price(active_asks, reverse=False)

        return order_book

    @property
    def transaction_price(self):
        """Returns the price of last transaction. If there are no transactions, returns None."""
        if not self.transactions or len(self.transactions) == 0:
            return None
        transactions = []
        for t in self.transactions:
            ts = t.get('timestamp')
            if hasattr(ts, "timestamp"):
                ts = ts.timestamp()
            transactions.append({'price': t.get('price'), 'timestamp': ts or 0})
        # sort by timestamp
        transactions.sort(key=lambda x: x['timestamp'])
        return transactions[-1]['price']

    async def initialize(self):
        self.start_time = now()
        self.active = True
        # Establish per-session exchanges/queues and start consuming trader messages.
        self.connection = await aio_pika.connect_robust(rabbitmq_url)
        self.channel = await self.connection.channel()

        await self.channel.declare_exchange(self.broadcast_exchange_name, aio_pika.ExchangeType.FANOUT,
                                            auto_delete=True)
        self.trader_exchange = await self.channel.declare_exchange(self.queue_name, aio_pika.ExchangeType.DIRECT,
                                                                   auto_delete=True)
        trader_queue = await self.channel.declare_queue(self.queue_name, auto_delete=True)
        await trader_queue.bind(self.trader_exchange)  # bind the queue to the exchange
        await trader_queue.consume(self.on_individual_message)  # Assuming you have a method named on_individual_message

        await trader_queue.purge()

    async def clean_up(self):
        """
        This one is mostly used for closing connections and channels opened by a trading session.
        At this stage we also dump existing transactions and orders from the memory. In the future, we'll probably
        dump them to a database.
        """
        # Signal the run loop to stop
        self._stop_requested.set()
        self.active = False
        try:
            # Unbind the queue from the exchange (optional, as auto_delete should handle this)
            trader_queue = await self.channel.get_queue(self.queue_name)
            await trader_queue.unbind(self.trader_exchange)

            # Close the channel and connection
            await self.channel.close()
            logger.info(f"Trading System {self.id} channel closed")
            await self.connection.close()
            logger.info(f"Trading System {self.id} connection closed")
            #     dump transactions and orders to files
            # await dump_transactions_to_csv(self.transactions, generate_file_name(self.id, "transactions"))
            # Dump all_orders to CSV
            # await dump_orders_to_csv(self.all_orders, generate_file_name(self.id, "all_orders"))
        except Exception as e:
            logger.error(f"An error occurred during cleanup: {e}")

    def get_active_orders_to_broadcast(self):
        # TODO. PHILIPP. It's not optimal but we'll rewrite it anyway when we convert form in-memory to DB
        if not self.active_orders:
            return []
        # lets keep only id, trader_id, order_type, amount, price, timestamp
        res = []
        for order in self.active_orders.values():
            res.append({
                'id': order['id'],
                'trader_id': order['trader_id'],
                'order_type': order['order_type'],
                'amount': order['amount'],
                'price': order['price'],
                'timestamp': order['timestamp'],
            })
        return res

    @staticmethod
    def _aggregate_orders_by_price(orders, reverse):
        # Collapse multiple orders at the same price into one level.
        totals = {}
        for order in orders:
            totals[order['price']] = totals.get(order['price'], 0) + order['amount']
        sorted_levels = sorted(totals.items(), key=lambda item: item[0], reverse=reverse)
        return [{'x': price, 'y': amount} for price, amount in sorted_levels]

    @staticmethod
    def _levels_to_map(levels):
        out = {}
        for level in levels or []:
            price = level.get("x")
            amount = level.get("y")
            if price is None:
                continue
            try:
                p = float(price)
                a = float(amount or 0)
            except (TypeError, ValueError):
                continue
            out[p] = a
        return out

    @staticmethod
    def _levels_from_map(level_map, reverse=False):
        prices = sorted(level_map.keys(), reverse=reverse)
        return [{"x": p, "y": level_map[p]} for p in prices]

    @classmethod
    def _compute_levels_delta(cls, previous_levels, current_levels):
        prev_map = cls._levels_to_map(previous_levels)
        curr_map = cls._levels_to_map(current_levels)
        added_or_updated = []
        removed = []
        for price, amount in curr_map.items():
            if price not in prev_map or prev_map[price] != amount:
                added_or_updated.append({"x": price, "y": amount})
        for price in prev_map:
            if price not in curr_map:
                removed.append(price)
        return {"added_or_updated": added_or_updated, "removed": removed}, curr_map

    @classmethod
    def _apply_levels_delta(cls, base_levels, delta, reverse=False):
        level_map = cls._levels_to_map(base_levels)
        payload = delta or {}
        for item in payload.get("added_or_updated") or []:
            price = item.get("x")
            amount = item.get("y")
            try:
                level_map[float(price)] = float(amount or 0)
            except (TypeError, ValueError):
                continue
        for price in payload.get("removed") or []:
            try:
                level_map.pop(float(price), None)
            except (TypeError, ValueError):
                continue
        return cls._levels_from_map(level_map, reverse=reverse)

    @staticmethod
    def _orders_to_map(orders):
        out = {}
        for order in orders or []:
            order_id = order.get("id")
            if order_id is None:
                continue
            out[str(order_id)] = dict(order)
        return out

    @classmethod
    def _compute_orders_delta(cls, previous_orders, current_orders):
        prev_map = cls._orders_to_map(previous_orders)
        curr_map = cls._orders_to_map(current_orders)
        added = []
        updated = []
        removed = []
        for oid, order in curr_map.items():
            if oid not in prev_map:
                added.append(order)
            elif prev_map[oid] != order:
                updated.append(order)
        for oid in prev_map:
            if oid not in curr_map:
                removed.append(oid)
        return {"added": added, "updated": updated, "removed": removed}, curr_map

    @staticmethod
    def _compute_balances_delta(previous_balances, current_balances):
        prev = previous_balances or {}
        curr = current_balances or {}
        delta = {}
        for key, curr_value in curr.items():
            prev_value = prev.get(key)
            if prev_value is None:
                delta[key] = curr_value
                continue
            try:
                diff = float(curr_value) - float(prev_value)
            except (TypeError, ValueError):
                if curr_value != prev_value:
                    delta[key] = curr_value
                continue
            if abs(diff) > 1e-9:
                delta[key] = diff
        return delta

    async def send_broadcast(self, message: dict, incoming_message=None):
        # TODO: PHILIPP: let's think how to make this more efficient but for simplicity
        # TODO we inject the current order book, active orders and transaction history into every broadcasted message
        # TODO: also important thing: we now send all active orders to everyone. We may think about possiblity to
        # TODO: send only to the trader who own them. But not now let's keep it simple.
        # let's set default type if type is emp[ty
        message['type'] = message.get('type', 'update')

        if message.get('type') == 'closure':
            if self.sql_enabled:
                try:
                    self.sql_store.save_message(
                        trading_session_uuid=self.id,
                        message_type="closure",
                        content=message,
                    )
                except Exception as exc:
                    logger.warning(f"SQL save_message(closure) failed: {exc}")
        else:
            # Broadcast compact deltas instead of full snapshots.
            spread, midpoint = self.get_spread()
            transactions = self.transactions or []
            alert_message, latest_price = self._build_alert_message(transactions)
            if not self._dividend_announced and self.start_time:
                elapsed = (now() - self.start_time).total_seconds()
                if elapsed >= 20:
                    dividend = random.choice([0, 8, 16, 24])
                    alert_message = f"Stock paid a dividend of: ${dividend}"
                    self._dividend_announced = True

            current_book = self.order_book
            bids_delta, next_bids_map = self._compute_levels_delta(
                self._levels_from_map(self._last_broadcast_book_levels.get("bids", {}), reverse=True),
                current_book.get("bids") or [],
            )
            asks_delta, next_asks_map = self._compute_levels_delta(
                self._levels_from_map(self._last_broadcast_book_levels.get("asks", {}), reverse=False),
                current_book.get("asks") or [],
            )
            self._last_broadcast_book_levels = {"bids": next_bids_map, "asks": next_asks_map}
            history_delta = []
            if transactions:
                latest_transaction = max(transactions, key=self._transaction_ts)
                history_delta = [latest_transaction]

            message.update({
                # TODO: PHILIPP: we need to think about the type of the message. it's hardcoded for now
                'order_book_delta': {
                    'bids': bids_delta,
                    'asks': asks_delta,
                },
                'history_delta': history_delta,
                'spread': spread,
                'midpoint': midpoint,
                'transaction_price': latest_price,
                'incoming_message': incoming_message
            })
            if alert_message:
                message['alert_message'] = alert_message
            logger.debug(
                f"Broadcast alert_message='{alert_message}' latest_price={latest_price}"
            )
            if self.sql_enabled:
                try:
                    self.sql_store.save_message(
                        trading_session_uuid=self.id,
                        message_type=str(message.get("type") or "update"),
                        content=message,
                    )
                except Exception as exc:
                    logger.warning(f"SQL save_message(broadcast) failed: {exc}")

        exchange = await self.channel.get_exchange(self.broadcast_exchange_name)
        await exchange.publish(
            aio_pika.Message(body=json.dumps(message, cls=CustomEncoder).encode()),
            routing_key=''  # routing_key is typically ignored in FANOUT exchanges
        )

    async def send_message_to_trader(self, trader_id, message):

        # TODO. PHILIPP. IT largely overlap with broadcast. We need to refactor that moving to _injection method
        current_price = self._latest_transaction_price(self.transactions or [])
        spread, mid_price = self.get_spread()
        current_book = self.order_book
        current_trader_orders = self.get_active_orders_for_trader(trader_id)
        current_balances = {
            "cash": self._cash_for_trader(trader_id),
            "shares": self._shares_for_trader(trader_id),
            "reserved_cash": self._reserved_cash_for_trader(trader_id),
            "available_cash": self._available_cash_for_trader(trader_id),
            "reserved_shares": self._reserved_shares_for_trader(trader_id),
            "available_shares": self._available_shares_for_trader(trader_id),
        }

        prev_book_levels = self._last_trader_book_levels.get(trader_id, {"bids": {}, "asks": {}})
        bids_delta, next_bids_map = self._compute_levels_delta(
            self._levels_from_map(prev_book_levels.get("bids", {}), reverse=True),
            current_book.get("bids") or [],
        )
        asks_delta, next_asks_map = self._compute_levels_delta(
            self._levels_from_map(prev_book_levels.get("asks", {}), reverse=False),
            current_book.get("asks") or [],
        )
        self._last_trader_book_levels[trader_id] = {"bids": next_bids_map, "asks": next_asks_map}

        prev_orders = self._last_trader_order_maps.get(trader_id, {})
        orders_delta, next_orders = self._compute_orders_delta(list(prev_orders.values()), current_trader_orders)
        self._last_trader_order_maps[trader_id] = next_orders

        prev_balances = self._last_trader_balances.get(trader_id, {})
        balances_delta = self._compute_balances_delta(prev_balances, current_balances)
        self._last_trader_balances[trader_id] = current_balances

        message.update({
            'type': 'update',  # TODO: PHILIPP: we need to think about the type of the message. it's hardcoded for now
            'order_book_delta': {
                'bids': bids_delta,
                'asks': asks_delta,
            },
            'orders_delta': orders_delta,
            'balances_delta': balances_delta,
            'transaction_count': int(self.trader_transaction_counts.get(trader_id, 0)),
            'spread': spread,
            'midpoint': mid_price,
            'mid_price': mid_price,
            'current_price': current_price,
        })
        if self.sql_enabled:
            try:
                self.sql_store.save_message(
                    trading_session_uuid=self.id,
                    message_type=str(message.get("type") or "update"),
                    content=message,
                    recipient_trader_uuid=str(trader_id or ""),
                )
            except Exception as exc:
                logger.warning(f"SQL save_message(trader) failed: {exc}")
        logger.debug(f"Trader {trader_id} current_price={current_price}")
        await self.trader_exchange.publish(
            aio_pika.Message(body=json.dumps(message, cls=CustomEncoder).encode()),
            routing_key=f'trader_{trader_id}'
        )

    @property
    def list_active_orders(self):
        """Returns a list of all active orders."""
        return list(self.active_orders.values())

    def get_orders_for_trader(self, trader_id):
        """Returns all orders (any status) that belong to the given trader."""
        return [
            order
            for order in self.all_orders.values()
            if order.get('trader_id') == trader_id
        ]

    def get_active_orders_for_trader(self, trader_id):
        """Returns only currently active orders for the given trader."""
        return [
            order
            for order in self.active_orders.values()
            if order.get('trader_id') == trader_id
        ]


    def place_order(self, order_dict: Dict):
        """ This one is called by handle_add_order, and is the one that actually places the order in the system.
        It adds automatically - we do all the validation (whether a trader allowed to place an order, etc) in the
        handle_add_order method.
        It doesn't make much sense to decouple handle_add_order with the actualy place_order now, but theoretically
        we may need this later if we want more speed for some traders that will be merged into trading platform (if the rabbitMQ solution won't be fast enough for simluation purposes).
        """
        trader_id = order_dict.get('trader_id')
        trader_meta = self.connected_traders.get(trader_id, {})
        if trader_meta.get('trader_type') == TraderType.NOISE.value:
            # Final write-time safety net for any future/bypassed submit path.
            order_dict['amount'] = 1
            try:
                order_dict['price'] = max(1, int(round(float(order_dict.get('price', self.default_price)))))
            except (TypeError, ValueError):
                order_dict['price'] = max(1, int(round(float(self.default_price))))

        order_id = order_dict['id']
        order_dict.update({
            'status': OrderStatus.ACTIVE.value,
        })
        self.all_orders[order_id] = order_dict
        return order_dict

    def get_spread(self):
        """
        Returns the spread and the midpoint. If there are no overlapping orders, returns None, None.
        """
        asks = [order for order in self.active_orders.values() if order['order_type'] == OrderType.ASK]
        bids = [order for order in self.active_orders.values() if order['order_type'] == OrderType.BID]

        # Sort by price (lowest first for asks), and then by timestamp (oldest first - FIFO)
        asks.sort(key=lambda x: (x['price'], x['timestamp']))
        # Sort by price (highest first for bids), and then by timestamp (oldest first)
        bids.sort(key=lambda x: (-x['price'], x['timestamp']))

        # Calculate the spread
        if asks and bids:
            lowest_ask = asks[0]['price']
            highest_bid = bids[0]['price']
            spread = lowest_ask - highest_bid
            mid_price = (lowest_ask + highest_bid) / 2
            return spread, mid_price
        else:
            logger.info("No overlapping orders.")
            return None, None

    def _aggressive_price_for_immediate(self, order_type: int, fallback_price):
        """
        For immediate execution:
        - immediate buy gets priced to cross the full current ask side
        - immediate sell gets priced to cross the full current bid side
        """
        asks = [order for order in self.active_orders.values() if order['order_type'] == OrderType.ASK]
        bids = [order for order in self.active_orders.values() if order['order_type'] == OrderType.BID]

        if int(order_type) == int(OrderType.BID):
            if asks:
                return max(order['price'] for order in asks)
        elif int(order_type) == int(OrderType.ASK):
            if bids:
                return min(order['price'] for order in bids)

        if fallback_price is None:
            return self.default_price
        try:
            return max(1, float(fallback_price))
        except (TypeError, ValueError):
            return self.default_price

    def create_transaction(self, bid, ask, transaction_price, transaction_amount=None):
        transaction = {
            "id": str(uuid.uuid4()),
            "trading_session_id": str(self.id),
            "bid_order_id": str(bid.get("id") or ""),
            "ask_order_id": str(ask.get("id") or ""),
            "timestamp": now(),
            "price": float(transaction_price),
            "quantity": float(transaction_amount or 0),
            "bid_trader_uuid": str(bid.get("trader_id") or ""),
            "ask_trader_uuid": str(ask.get("trader_id") or ""),
        }
        self._transactions_mem.append(transaction)
        if self.sql_enabled:
            try:
                self.sql_store.save_transaction(
                    trading_session_uuid=self.id,
                    bid_trader_uuid=transaction["bid_trader_uuid"],
                    ask_trader_uuid=transaction["ask_trader_uuid"],
                    bid_order_id=transaction["bid_order_id"],
                    ask_order_id=transaction["ask_order_id"],
                    price=transaction["price"],
                    timestamp=transaction["timestamp"],
                    transaction_id=transaction["id"],
                    transaction_json=transaction,
                )
            except Exception as exc:
                logger.warning(f"SQL save_transaction failed: {exc}")

        # Append to self.transactions

        # Log the transaction creation
        logger.info(f"Transaction created: {transaction}")

        # Return trader IDs involved in the transaction for further processing
        return ask['trader_id'], bid['trader_id'], transaction

    def _apply_fill(self, order_id, fill_amount):
        """Apply a partial or full fill to one active order."""
        order = self.all_orders[order_id]
        remaining = float(order['amount']) - float(fill_amount)
        if remaining <= 0:
            order['amount'] = 0
            order['status'] = OrderStatus.EXECUTED.value
            order['execution_timestamp'] = now()
        else:
            order['amount'] = remaining
            order['status'] = OrderStatus.ACTIVE.value
        if self.sql_enabled:
            try:
                self.sql_store.save_order(self.id, order)
            except Exception as exc:
                logger.warning(f"SQL save_order(_apply_fill) failed: {exc}")

    def _cash_for_trader(self, trader_id):
        trader_data = self.connected_traders.get(trader_id, {})
        cash = trader_data.get('cash')
        if cash is None:
            return None
        try:
            return float(cash)
        except (TypeError, ValueError):
            return None

    def _reserved_cash_for_trader(self, trader_id):
        trader_data = self.connected_traders.get(trader_id, {})
        reserved_cash = trader_data.get('reserved_cash', 0)
        try:
            return max(0.0, float(reserved_cash))
        except (TypeError, ValueError):
            return 0.0

    def _shares_for_trader(self, trader_id):
        trader_data = self.connected_traders.get(trader_id, {})
        shares = trader_data.get('shares')
        if shares is None:
            return None
        try:
            return float(shares)
        except (TypeError, ValueError):
            return None

    def _reserved_shares_for_trader(self, trader_id):
        trader_data = self.connected_traders.get(trader_id, {})
        reserved_shares = trader_data.get('reserved_shares', 0)
        try:
            return max(0.0, float(reserved_shares))
        except (TypeError, ValueError):
            return 0.0

    def _available_cash_for_trader(self, trader_id):
        cash = self._cash_for_trader(trader_id)
        if cash is None:
            return None
        return max(0.0, cash - self._reserved_cash_for_trader(trader_id))

    def _available_shares_for_trader(self, trader_id):
        shares = self._shares_for_trader(trader_id)
        if shares is None:
            return None
        return max(0.0, shares - self._reserved_shares_for_trader(trader_id))

    def _reserve_cash_for_bid(self, trader_id, price, amount):
        """
        Reserve cash for a bid order at placement time.
        Returns (ok, reserved_amount).
        """
        trader_data = self.connected_traders.get(trader_id)
        if trader_data is None:
            return True, 0.0

        available_cash = self._available_cash_for_trader(trader_id)
        if available_cash is None:
            return True, 0.0
        try:
            reserve_amount = max(0.0, float(price) * float(amount))
        except (TypeError, ValueError):
            return False, 0.0
        if reserve_amount > (available_cash + 1e-9):
            return False, reserve_amount

        trader_data['reserved_cash'] = self._reserved_cash_for_trader(trader_id) + reserve_amount
        return True, reserve_amount

    def _release_reserved_cash(self, trader_id, amount):
        trader_data = self.connected_traders.get(trader_id)
        if trader_data is None:
            return
        try:
            release_amount = max(0.0, float(amount))
        except (TypeError, ValueError):
            return
        current_reserved = self._reserved_cash_for_trader(trader_id)
        trader_data['reserved_cash'] = max(0.0, current_reserved - release_amount)

    def _reserve_shares_for_ask(self, trader_id, amount):
        """
        Reserve shares for an ask order at placement time.
        Returns (ok, reserved_amount).
        """
        trader_data = self.connected_traders.get(trader_id)
        if trader_data is None:
            return True, 0.0

        available_shares = self._available_shares_for_trader(trader_id)
        if available_shares is None:
            return True, 0.0
        try:
            reserve_amount = max(0.0, float(amount))
        except (TypeError, ValueError):
            return False, 0.0
        if reserve_amount > (available_shares + 1e-9):
            return False, reserve_amount

        trader_data['reserved_shares'] = self._reserved_shares_for_trader(trader_id) + reserve_amount
        return True, reserve_amount

    def _release_reserved_shares(self, trader_id, amount):
        trader_data = self.connected_traders.get(trader_id)
        if trader_data is None:
            return
        try:
            release_amount = max(0.0, float(amount))
        except (TypeError, ValueError):
            return
        current_reserved = self._reserved_shares_for_trader(trader_id)
        trader_data['reserved_shares'] = max(0.0, current_reserved - release_amount)

    def _apply_share_flows(self, bid_trader_id, ask_trader_id, transaction_amount):
        buyer = self.connected_traders.get(bid_trader_id)
        seller = self.connected_traders.get(ask_trader_id)
        if buyer is not None and buyer.get('shares') is not None:
            try:
                buyer['shares'] = float(buyer['shares']) + float(transaction_amount)
            except (TypeError, ValueError):
                buyer['shares'] = float(transaction_amount)
        if seller is not None and seller.get('shares') is not None:
            try:
                seller['shares'] = max(0.0, float(seller['shares']) - float(transaction_amount))
            except (TypeError, ValueError):
                seller['shares'] = 0.0

    def _apply_cash_flows(self, bid_trader_id, ask_trader_id, transaction_price, transaction_amount):
        trade_value = float(transaction_price) * float(transaction_amount)
        buyer = self.connected_traders.get(bid_trader_id)
        seller = self.connected_traders.get(ask_trader_id)
        if buyer is not None and buyer.get('cash') is not None:
            try:
                buyer['cash'] = max(0.0, float(buyer['cash']) - trade_value)
            except (TypeError, ValueError):
                buyer['cash'] = 0.0
        if seller is not None and seller.get('cash') is not None:
            try:
                seller['cash'] = float(seller['cash']) + trade_value
            except (TypeError, ValueError):
                seller['cash'] = trade_value

    async def clear_orders(self):
        """ this goes through order book trying to execute orders """
        # Keep a summary of matches for targeted updates back to traders.
        res = {'transactions': [], 'removed_active_orders': []}
        # Separate active orders into asks and bids
        asks = [order for order in self.active_orders.values() if order['order_type'] == OrderType.ASK]
        bids = [order for order in self.active_orders.values() if order['order_type'] == OrderType.BID]

        # Sort by price (lowest first for asks), and then by timestamp (oldest first - FIFO)
        # TODO: remember that this is FIFO. We need to adjust the rule (or make it adjustable in the config) if we want
        asks.sort(key=lambda x: (x['price'], x['timestamp']))
        # Sort by price (highest first for bids), and then by timestamp (oldest first)
        bids.sort(key=lambda x: (-x['price'], x['timestamp']))

        if not asks or not bids:
            logger.info("No overlapping orders.")
            return res

        transactions = []
        traders_to_transactions_lookup = defaultdict(list)
        # Create transactions with price-time priority and partial fills.
        while asks and bids:
            ask = asks[0]
            bid = bids[0]
            if ask['price'] > bid['price']:
                break

            transaction_price = (ask['price'] + bid['price']) / 2
            transaction_amount = min(float(ask['amount']), float(bid['amount']))
            ask_trader_type = self.connected_traders[ask['trader_id']]['trader_type']
            ask_trader_id = ask.get('trader_id')
            bid_trader_id = bid.get('trader_id')
            if (not self.allow_self_trade) and ask_trader_type == TraderType.HUMAN.value and ask_trader_id == bid_trader_id:
                logger.warning(f'Blocking self-execution for trader {ask_trader_id}')
                break

            # Guarantee non-negative cash by limiting buy fills to affordable amount.
            buyer_cash = self._cash_for_trader(bid_trader_id)
            if buyer_cash is not None and transaction_price > 0:
                max_affordable = math.floor(buyer_cash / float(transaction_price))
                if max_affordable <= 0:
                    self._release_reserved_cash(
                        bid_trader_id,
                        float(bid.get('price', 0)) * float(bid.get('amount', 0))
                    )
                    self.all_orders[bid['id']]['status'] = OrderStatus.CANCELLED.value
                    self.all_orders[bid['id']]['cancellation_timestamp'] = now()
                    if self.sql_enabled:
                        try:
                            self.sql_store.save_order(self.id, self.all_orders[bid['id']])
                        except Exception as exc:
                            logger.warning(f"SQL save_order(cancel-unaffordable) failed: {exc}")
                    bids.pop(0)
                    continue
                transaction_amount = min(transaction_amount, float(max_affordable))
                if transaction_amount <= 0:
                    bids.pop(0)
                    continue

            _, _, transaction = self.create_transaction(bid, ask, transaction_price, transaction_amount)
            # Release reserved cash for the filled bid quantity at the bid limit price.
            self._release_reserved_cash(
                bid_trader_id,
                float(bid.get('price', 0)) * float(transaction_amount),
            )
            # Release reserved shares for the filled ask quantity.
            self._release_reserved_shares(
                ask_trader_id,
                float(transaction_amount),
            )
            self._apply_fill(ask['id'], transaction_amount)
            self._apply_fill(bid['id'], transaction_amount)
            self._apply_cash_flows(bid_trader_id, ask_trader_id, transaction_price, transaction_amount)
            self._apply_share_flows(bid_trader_id, ask_trader_id, transaction_amount)
            self.trader_transaction_counts[ask_trader_id] += 1
            self.trader_transaction_counts[bid_trader_id] += 1

            # No need to append the transaction to self.transactions here as it's handled within create_transaction

            # we  need to get the trader_in_transcation_lookup and if it's
            # not there we need to create it.
            # let's not add the entire transaction here, just the order id, price, type of order, amount - so they can correclty update the inventory
            traders_to_transactions_lookup[ask['trader_id']].append(
                {
                    'id': ask['id'],
                    'price': transaction_price,
                    'type': 'ask',
                    'amount': transaction_amount,
                    'timestamp': transaction.get('timestamp'),
                    'transaction_id': transaction.get('id'),
                }
            )
            traders_to_transactions_lookup[bid['trader_id']].append(
                {
                    'id': bid['id'],
                    'price': transaction_price,
                    'type': 'bid',
                    'amount': transaction_amount,
                    'timestamp': transaction.get('timestamp'),
                    'transaction_id': transaction.get('id'),
                }
            )

            if self.all_orders[ask['id']]['status'] == OrderStatus.EXECUTED.value:
                asks.pop(0)
            if self.all_orders[bid['id']]['status'] == OrderStatus.EXECUTED.value:
                bids.pop(0)

            transactions.append(transaction)

        # if transactions are not empty (so there are new transactions) let's form subgroup_broadcast message
        # that will contain to_whom (traders who participated in transactions) and the list of transactions and add them to res
        if transactions:
            res['subgroup_broadcast'] = traders_to_transactions_lookup

        return res

    @if_active
    async def handle_add_order(self, data: dict):
        """
        that is called automatically on the incoming message if type of a message is 'add_order'.
        it returns the dict with respond=True to signalize that (ideally) we need to ping back the trader who sent the
        message that everything is ok (it's not needed in the current implementation and perhaps we can get rid of this later because it also increase the info load)

        """

        immediate = bool(data.pop('immediate', False))
        data['order_type'] = int(data['order_type'])
        trader_id = data.get('trader_id')
        trader_meta = self.connected_traders.get(trader_id, {})
        trader_type = trader_meta.get('trader_type')

        # Hard safety rule: all noise-trader orders are unit-size and integer-priced.
        if trader_type == TraderType.NOISE.value:
            data['amount'] = 1
            try:
                data['price'] = max(1, int(round(float(data.get('price', self.default_price)))))
            except (TypeError, ValueError):
                data['price'] = max(1, int(round(float(self.default_price))))

        placed_order_id = None
        reserved_cash_amount = 0.0
        reserved_cash_trader_id = None
        reserved_shares_amount = 0.0
        reserved_shares_trader_id = None
        if immediate:
            data['price'] = self._aggressive_price_for_immediate(
                data['order_type'],
                data.get('price')
            )
        try:

            order = Order(status=OrderStatus.BUFFERED.value,
                          session_id=self.id,
                          **data)

            if int(order.order_type) == int(OrderType.BID):
                ok, reserved_cash_amount = self._reserve_cash_for_bid(
                    order.trader_id,
                    order.price,
                    order.amount,
                )
                if not ok:
                    return dict(
                        respond=True,
                        status="failed",
                        reason="Insufficient available cash for buy order",
                    )
                reserved_cash_trader_id = order.trader_id
            elif int(order.order_type) == int(OrderType.ASK):
                ok, reserved_shares_amount = self._reserve_shares_for_ask(
                    order.trader_id,
                    order.amount,
                )
                if not ok:
                    return dict(
                        respond=True,
                        status="failed",
                        reason="Insufficient available shares for sell order",
                    )
                reserved_shares_trader_id = order.trader_id

            # Place the order
            placed_order = self.place_order(order.model_dump())  # Converting order to dict for compatibility
            placed_order_id = placed_order.get('id')
            if self.sql_enabled:
                try:
                    self.sql_store.save_order(self.id, placed_order)
                except Exception as exc:
                    logger.warning(f"SQL save_order(new) failed: {exc}")

        except ValidationError as e:
            # Handle validation errors, e.g., log them or send a message back to the trader
            logger.critical(f"Order validation failed: {e}")
            if reserved_cash_trader_id and reserved_cash_amount > 0:
                self._release_reserved_cash(reserved_cash_trader_id, reserved_cash_amount)
            if reserved_shares_trader_id and reserved_shares_amount > 0:
                self._release_reserved_shares(reserved_shares_trader_id, reserved_shares_amount)
            return dict(respond=True, status="failed", reason="Order validation failed")
        except Exception:
            if reserved_cash_trader_id and reserved_cash_amount > 0:
                self._release_reserved_cash(reserved_cash_trader_id, reserved_cash_amount)
            if reserved_shares_trader_id and reserved_shares_amount > 0:
                self._release_reserved_shares(reserved_shares_trader_id, reserved_shares_amount)
            raise
        # lets clear them now
        resp = await self.clear_orders()
        # IOC semantics: after immediate matching cycle, cancel any remaining quantity.
        if immediate and placed_order_id and placed_order_id in self.all_orders:
            if self.all_orders[placed_order_id]['status'] == OrderStatus.ACTIVE.value:
                if int(self.all_orders[placed_order_id].get('order_type')) == int(OrderType.BID):
                    self._release_reserved_cash(
                        self.all_orders[placed_order_id].get('trader_id'),
                        float(self.all_orders[placed_order_id].get('price', 0))
                        * float(self.all_orders[placed_order_id].get('amount', 0))
                    )
                elif int(self.all_orders[placed_order_id].get('order_type')) == int(OrderType.ASK):
                    self._release_reserved_shares(
                        self.all_orders[placed_order_id].get('trader_id'),
                        float(self.all_orders[placed_order_id].get('amount', 0)),
                    )
                self.all_orders[placed_order_id]['status'] = OrderStatus.CANCELLED.value
                self.all_orders[placed_order_id]['cancellation_timestamp'] = now()
                if self.sql_enabled:
                    try:
                        self.sql_store.save_order(self.id, self.all_orders[placed_order_id])
                    except Exception as exc:
                        logger.warning(f"SQL save_order(ioc-cancel) failed: {exc}")
        subgroup_data = resp.pop('subgroup_broadcast', None)
        if subgroup_data:
            await self.send_message_to_subgroup(subgroup_data)
        return dict(respond=True, **resp)

    async def send_message_to_subgroup(self, message):
        for trader_id, transaction_list in message.items():
            await self.send_message_to_trader(trader_id, {'type': 'update', 'new_transactions': transaction_list})

    @if_active
    async def handle_cancel_order(self, data: dict):
        order_id = data.get('order_id')
        trader_id = data.get('trader_id')

        # Ensure order_id is a valid UUID
        try:
            order_id = uuid.UUID(order_id)
        except ValueError:
            logger.warning(f"Invalid order ID format: {order_id}.")
            return {"status": "failed", "reason": "Invalid order ID format"}

        async with self.lock:
            # Check if the order exists and belongs to the trader
            if order_id not in self.active_orders:
                return {"status": "failed", "reason": "Order not found"}

            existing_order = self.active_orders[order_id]

            if existing_order['trader_id'] != trader_id:
                logger.warning(f"Trader {trader_id} does not own order {order_id}.")
                return {"status": "failed", "reason": "Trader does not own the order"}

            if existing_order['status'] != OrderStatus.ACTIVE.value:
                logger.warning(f"Order {order_id} is not active and cannot be canceled.")
                return {"status": "failed", "reason": "Order is not active"}

            # Cancel the order
            if int(existing_order.get('order_type')) == int(OrderType.BID):
                self._release_reserved_cash(
                    trader_id,
                    float(existing_order.get('price', 0)) * float(existing_order.get('amount', 0)),
                )
            elif int(existing_order.get('order_type')) == int(OrderType.ASK):
                self._release_reserved_shares(
                    trader_id,
                    float(existing_order.get('amount', 0)),
                )
            self.all_orders[order_id]['status'] = OrderStatus.CANCELLED.value
            self.all_orders[order_id]['cancellation_timestamp'] = now()
            if self.sql_enabled:
                try:
                    self.sql_store.save_order(self.id, self.all_orders[order_id])
                except Exception as exc:
                    logger.warning(f"SQL save_order(cancel) failed: {exc}")

            return {"status": "cancel success", "order": order_id, "respond": True}

    @if_active
    async def handle_register_me(self, msg_body):
        trader_id = msg_body.get('trader_id')
        trader_type = msg_body.get('trader_type')
        cash = msg_body.get('cash')
        shares = msg_body.get('shares')
        self.connected_traders[trader_id] = {
            'trader_type': trader_type,
            'cash': cash,
            'shares': shares,
            'reserved_cash': 0.0,
            'reserved_shares': 0.0,
        }
        # Reset delta baselines for this trader (important on websocket reconnect).
        self._last_trader_book_levels[trader_id] = {"bids": {}, "asks": {}}
        self._last_trader_order_maps[trader_id] = {}
        self._last_trader_balances[trader_id] = {}

        logger.info(f"Trader type  {trader_type} id {trader_id} connected.")
        logger.info(f"Total connected traders: {len(self.connected_traders)}")
        return dict(respond=True, trader_id=trader_id, message="Registered successfully", individual=True)

    async def on_individual_message(self, message):
        incoming_message = json.loads(message.body.decode())
        logger.info(f"TS {self.id} received message: {incoming_message}")
        action = incoming_message.pop('action', None)
        trader_id = incoming_message.get('trader_id', None)  # Assuming the trader_id is part of the message
        if incoming_message is None:
            logger.critical(f"Invalid message format: {message}")

        if action:
            handler_method = getattr(self, f"handle_{action}", None)
            if handler_method:

                result = await handler_method(incoming_message)
                if result and result.pop('respond', None) and trader_id:
                    await self.send_message_to_trader(trader_id, result)
                    #         TODO.PHILIPP. IMPORTANT! let's at this stage also send a broadcast message to all traders with updated info.
                    # IT IS FAR from optimal but for now we keep it simple. We'll refactor it later.
                    # Broadcast after handling an action so all traders see the updated snapshot.
                    if not result.get('individual', False):
                        await self.send_broadcast(message=dict(text="book is updated"), incoming_message=incoming_message)



            else:
                logger.warning(f"No handler method found for action: {action}")
        else:
            logger.warning(f"No action found in message: {incoming_message}")

    async def run(self):
        try:
            while not self._stop_requested.is_set():
                current_time = now()
                if current_time - self.start_time > timedelta(
                        minutes=self.duration
                ):
                    # Time limit reached: stop accepting orders and broadcast closure.
                    logger.critical('Time limit reached, stopping...')
                    self.active = False  # here we stop accepting all incoming requests on placing new orders, cancelling etc.
                    await self.send_broadcast({"type": "closure"})

                    break
                await asyncio.sleep(1)
            logger.critical('Exited the run loop.')
        except asyncio.CancelledError:
            logger.info('Run method cancelled, performing cleanup of trading session...')

            raise

        except Exception as e:
            # Handle the exception here
            logger.error(f"Exception in trading session run: {e}")
            # Optionally re-raise the exception if you want it to be propagated
            raise
        finally:
            await self.clean_up()
