import asyncio
import aio_pika
import json
import uuid
from structures.structures import OrderType, ActionType, TraderType
import os

from main_platform.custom_logger import setup_custom_logger
from main_platform.utils import (CustomEncoder)

rabbitmq_url = os.getenv('RABBITMQ_URL', 'amqp://localhost')

logger = setup_custom_logger(__name__)


class BaseTrader:
    orders: list = []
    order_book: dict = {}
    active_orders: list = []
    cash = 0
    shares = 0
    initial_cash = 0
    initial_shares = 0

    def __init__(self, trader_type: TraderType, cash=0, shares=0):

        self.initial_shares = shares
        self.initial_cash = cash
        self.cash = cash
        self.shares = shares
        self.initial_cash = cash
        self.initial_shares = shares

        self._stop_requested = asyncio.Event()  # this one we need only for traders which should be kept active in loop. For instance human traders don't need that
        self.trader_type = trader_type.value
        self.id = str(uuid.uuid4())
        logger.info(f"Trader of type {self.trader_type} created with UUID: {self.id}")
        self.connection = None
        self.channel = None
        self.trading_session_uuid = None
        self.trading_session = None
        self.trader_queue_name = f'trader_{self.id}'  # unique queue name based on Trader's UUID
        logger.info(f"Trader queue name: {self.trader_queue_name}")
        self.queue_name = None
        self.broadcast_exchange_name = None
        self.trading_system_exchange = None

        self.start_time = asyncio.get_event_loop().time()
        self.full_orders = []
        self.last_transaction_price = None

    def get_elapsed_time(self) -> float:
        """Returns the elapsed time in seconds since the trader was initialized."""
        current_time = asyncio.get_event_loop().time()
        return current_time - self.start_time

    @property
    def delta_cash(self):
        return self.cash - self.initial_cash

    async def initialize(self):
        self.connection = await aio_pika.connect_robust(rabbitmq_url)
        self.channel = await self.connection.channel()
        await self.channel.declare_queue(self.trader_queue_name, auto_delete=True)

    async def clean_up(self):
        self._stop_requested.set()
        try:
            # Close the channel and connection
            if self.channel:
                await self.channel.close()
                logger.info(f"Trader {self.id} channel closed")
            if self.connection:
                await self.connection.close()
                logger.info(f"Trader {self.id} connection closed")

        except Exception as e:
            logger.error(f"An error occurred during Trader cleanup: {e}")

    async def connect_to_session(self, trading_session_uuid):
        self.trading_session_uuid = trading_session_uuid
        self.queue_name = f'trading_system_queue_{self.trading_session_uuid}'
        self.trader_queue_name = f'trader_{self.id}'  # unique queue name based on Trader's UUID

        self.broadcast_exchange_name = f'broadcast_{self.trading_session_uuid}'

        # Subscribe to group messages
        broadcast_exchange = await self.channel.declare_exchange(self.broadcast_exchange_name,
                                                                 aio_pika.ExchangeType.FANOUT,
                                                                 auto_delete=False)
        broadcast_queue = await self.channel.declare_queue("", auto_delete=True)
        await broadcast_queue.bind(broadcast_exchange)
        await broadcast_queue.consume(self.on_message_from_system)

        # For individual messages
        self.trading_system_exchange = await self.channel.declare_exchange(self.queue_name,
                                                                           aio_pika.ExchangeType.DIRECT,
                                                                           auto_delete=False)
        trader_queue = await self.channel.declare_queue(
            self.trader_queue_name,
            auto_delete=True
        )  # Declare a unique queue for this Trader
        await trader_queue.bind(self.trading_system_exchange, routing_key=self.trader_queue_name)
        await trader_queue.consume(self.on_message_from_system)

        await self.register()  # Register with the trading system

    async def register(self):
        message = {
            'type': ActionType.REGISTER.value,
            'action': ActionType.REGISTER.value,
            'trader_type': self.trader_type,
            'cash': self.cash,
            'shares': self.shares,
        }

        await self.send_to_trading_system(message)

    async def send_to_trading_system(self, message):
        # front end design means human traders' own_orders will alaways be empty
        message['trader_id'] = self.id
        await self.trading_system_exchange.publish(
            aio_pika.Message(body=json.dumps(message, cls=CustomEncoder).encode()),
            routing_key=self.queue_name  # Use the dynamic queue_name
        )

    async def on_message_from_system(self, message):
        """Process incoming messages from trading system.
        For BaseTrader it updates order book and inventory if needed.

        """
        try:

            json_message = json.loads(message.body.decode())

            action_type = json_message.get('type')
            data = json_message
            # First update local market snapshot/inventory, then call specific handlers.
            if data.get('new_transactions'):
                self.update_inventory(data['new_transactions'])
            if not data:
                logger.error('no data from trading system')
                return
            price_hint = data.get('transaction_price')
            if price_hint is None:
                price_hint = data.get('current_price')
            if price_hint is not None:
                try:
                    numeric_price = float(price_hint)
                    if numeric_price > 0:
                        self.last_transaction_price = numeric_price
                except (TypeError, ValueError):
                    pass
            order_book = data.get('order_book')
            if order_book is not None:
                self.order_book = order_book
            order_book_delta = data.get('order_book_delta')
            if order_book_delta is not None:
                self.order_book = self._apply_order_book_delta(self.order_book, order_book_delta)
            active_orders = data.get('active_orders')
            if active_orders is not None:
                self.active_orders = active_orders
                own_orders = [order for order in active_orders if order['trader_id'] == self.id]
                # lets convert the order list to a dictionary with keys as order ids
                self.orders = own_orders
            orders_delta = data.get('orders_delta')
            if orders_delta is not None:
                self.orders = self._apply_orders_delta(self.orders, orders_delta)

            handler = getattr(self, f'handle_{action_type}', None)
            if handler:
                await handler(data)
            elif action_type not in (None, "update"):
                logger.warning(f"Unhandled message type: {action_type}")
            await self.post_processing_server_message(data)

        except json.JSONDecodeError:
            logger.error(f"Error decoding message: {message}")

    @staticmethod
    def _apply_order_book_delta(current_order_book, order_book_delta):
        base = current_order_book or {"bids": [], "asks": []}
        result = {
            "bids": list(base.get("bids") or []),
            "asks": list(base.get("asks") or []),
        }

        def apply_side(side_name, reverse=False):
            side_delta = (order_book_delta or {}).get(side_name) or {}
            levels = {float(item.get("x")): float(item.get("y") or 0) for item in result.get(side_name, []) if item.get("x") is not None}
            for item in side_delta.get("added_or_updated") or []:
                try:
                    levels[float(item.get("x"))] = float(item.get("y") or 0)
                except (TypeError, ValueError):
                    continue
            for price in side_delta.get("removed") or []:
                try:
                    levels.pop(float(price), None)
                except (TypeError, ValueError):
                    continue
            sorted_prices = sorted(levels.keys(), reverse=reverse)
            result[side_name] = [{"x": p, "y": levels[p]} for p in sorted_prices]

        apply_side("bids", reverse=True)
        apply_side("asks", reverse=False)
        return result

    @staticmethod
    def _apply_orders_delta(current_orders, orders_delta):
        order_map = {}
        for order in current_orders or []:
            order_id = order.get("id")
            if order_id is None:
                continue
            order_map[str(order_id)] = order

        for order_id in (orders_delta or {}).get("removed") or []:
            order_map.pop(str(order_id), None)

        for order in (orders_delta or {}).get("added") or []:
            order_id = order.get("id")
            if order_id is None:
                continue
            order_map[str(order_id)] = order

        for order in (orders_delta or {}).get("updated") or []:
            order_id = order.get("id")
            if order_id is None:
                continue
            order_map[str(order_id)] = order

        return list(order_map.values())

    def update_inventory(self, new_transactions):
        """
        new transactions come in format:
         [{'id': 'aa5b7bd0-6bd1-49ab-ab24-fbd46b3d437a', 'price': 1999.0, 'type': 'ask', 'amount': 1.0}, {'id': 'e5712c5d-dc58-4092-b369-b9306f2f0527', 'price': 1999.0, 'type': 'bid', 'amount': 1.0}]

        and we need to update self.shares and self.cash accordingly
        """
        for transaction in new_transactions:

            if transaction['type'] == 'ask':
                self.shares -= transaction['amount']
                self.cash += transaction['price'] * transaction['amount']
            else:
                self.shares += transaction['amount']
                self.cash -= transaction['price'] * transaction['amount']
        if self.cash < 0:
            logger.error(f"Trader {self.id} cash dropped below zero; clamping to zero.")
            self.cash = 0
        if self.trader_type == TraderType.HUMAN.value:
            logger.info(f"Trader {self.id} updated inventory: shares: {self.shares}, cash: {self.cash}")

    async def post_processing_server_message(self, json_message):
        """for BaseTrader it is not implemented. For human trader we send updated info back to client.
        For other market maker types we need do some reactions on updated market if needed.
        """
        pass

    async def post_new_order(self,
                             amount, price, order_type: OrderType, immediate: bool = False
                             ):
        new_order = {
            "action": ActionType.POST_NEW_ORDER.value,
            "amount": amount,
            "price": price,
            "order_type": order_type,
        }
        if immediate:
            new_order["immediate"] = True
        await self.send_to_trading_system(new_order)
        logger.debug(f"Trader {self.id} posted new {order_type} order: {new_order}")

    async def send_cancel_order_request(self, order_id: uuid.UUID):
        if not order_id:
            logger.error(f"Order ID is not provided")
            return
        if not self.orders:
            logger.error(f"Trader {self.id} has no active orders")
            return
        if order_id not in [order['id'] for order in self.orders]:
            logger.error(f"Trader {self.id} has no order with ID {order_id}")
            return

        cancel_order_request = {
            "action": ActionType.CANCEL_ORDER.value,  # Assuming you have an ActionType Enum
            "trader_id": self.id,
            "order_id": order_id
        }

        await self.send_to_trading_system(cancel_order_request)
        logger.info(f"Trader {self.id} sent cancel order request: {cancel_order_request}")

    async def run(self):
        # Placeholder method for compatibility with the trading system
        logger.info(f"trader {self.id} is waiting")
        pass

    async def handle_closure(self, data):
        """Handle closure messages from the trading system."""
        logger.critical(
            f"Trader {self.id}: type: {self.trader_type}. Closure signal received. Preparing to stop trading activities.")

        self._stop_requested.set()
        await self.clean_up()
