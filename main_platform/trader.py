import asyncio
import aio_pika
import json
import uuid
import random
from structures.structures import OrderModel, OrderStatus, OrderType, ActionType, TraderType
from datetime import datetime
from main_platform.utils import ack_message, convert_to_noise_state, convert_to_book_format, convert_to_trader_actions
from main_platform.custom_logger import setup_custom_logger
from pprint import pprint
from external_traders.noise_trader import get_noise_rule, get_signal_noise, settings_noise, settings, get_noise_rule_unif
import numpy as np

logger = setup_custom_logger(__name__)


class Trader:
    orders = []
    all_orders = []
    transactions = []

    def __init__(self, trader_type: TraderType):
        self.trader_type = trader_type.value
        self.id = str(uuid.uuid4())
        print(f"Trader created with UUID: {self.id}")
        self.connection = None
        self.channel = None
        self.trading_session_uuid = None
        self.trader_queue_name = f'trader_{self.id}'  # unique queue name based on Trader's UUID
        print(f"Trader queue name: {self.trader_queue_name}")
        self.queue_name = None
        self.broadcast_exchange_name = None
        self.trading_system_exchange = None

    async def initialize(self):
        self.connection = await aio_pika.connect_robust("amqp://localhost")
        self.channel = await self.connection.channel()
        await self.channel.declare_queue(self.trader_queue_name, auto_delete=True)

    async def clean_up(self):
        try:
            # Close the channel and connection
            if self.channel:
                await self.channel.close()
                logger.info(f"Trader {self.id} channel closed")
            if self.connection:
                await self.connection.close()
                logger.info(f"Trader {self.id} connection closed")

        except Exception as e:
            print(f"An error occurred during Trader cleanup: {e}")

    async def connect_to_session(self, trading_session_uuid):
        self.trading_session_uuid = trading_session_uuid
        self.queue_name = f'trading_system_queue_{self.trading_session_uuid}'
        self.trader_queue_name = f'trader_{self.id}'  # unique queue name based on Trader's UUID

        self.broadcast_exchange_name = f'broadcast_{self.trading_session_uuid}'

        # Subscribe to group messages
        broadcast_exchange = await self.channel.declare_exchange(self.broadcast_exchange_name,
                                                                 aio_pika.ExchangeType.FANOUT,
                                                                 auto_delete=True)
        broadcast_queue = await self.channel.declare_queue("", auto_delete=True)
        await broadcast_queue.bind(broadcast_exchange)
        await broadcast_queue.consume(self.on_message)

        # For individual messages
        self.trading_system_exchange = await self.channel.declare_exchange(self.queue_name,
                                                                           aio_pika.ExchangeType.DIRECT,
                                                                           auto_delete=True)
        trader_queue = await self.channel.declare_queue(
            self.trader_queue_name,
            auto_delete=True
        )  # Declare a unique queue for this Trader
        await trader_queue.bind(self.trading_system_exchange, routing_key=self.trader_queue_name)
        await trader_queue.consume(self.on_message)  # Assuming you have a method named on_message

        await self.register()  # Register with the trading system

    async def register(self):
        message = {
            'action': ActionType.REGISTER.value,
            'trader_type': self.trader_type
        }

        await self.send_to_trading_system(message)

    async def send_to_trading_system(self, message):
        # we add to any message the trader_id
        message['trader_id'] = str(self.id)
        await self.trading_system_exchange.publish(
            aio_pika.Message(body=json.dumps(message).encode()),
            routing_key=self.queue_name  # Use the dynamic queue_name
        )

    @ack_message
    async def on_message(self, message):
        """This method is called whenever a message is received by the Trader"""

        resp = json.loads(message.body.decode())
        # logger.info(f"Trader {self.id} received message: {resp}")
        #     # TODO: the following two lines are currently some artefacts, they should be removed later.
        #     # currently we broadcast the updated active orders and transactions to all traders.
        #     # in the future we should only broadcast the updated order book and let the traders decide
        #     # because now it is totally deanonymized; it is a bad idea to broadcast all the orders and transactions

        if resp.get('orders'):
            self.all_orders = resp.get('orders')
            self.orders = self.get_my_orders(self.all_orders)

    async def request_order_book(self):
        message = {
            "action": ActionType.UPDATE_BOOK_STATUS.value,
        }

        await self.send_to_trading_system(message)

    async def post_new_order(self,
                             amount, price, order_type: OrderType
                             ):
        # todo: here we should call a generating function passing there the current book state etc,
        # and it will return price, amount, order_type

        # TODO: all the following should be removed, it's now only for generating some prices for bids and asks

        new_order = {

            "action": ActionType.POST_NEW_ORDER.value,
            "amount": amount,
            "price": price,
            "order_type": order_type.value,
        }

        resp = await self.send_to_trading_system(new_order)

        logger.debug(f"Trader {self.id} posted new {order_type} order: {new_order}")

    def get_my_transactions(self, transactions):
        """filter full transactions to get only mine"""
        return [transaction for transaction in transactions if transaction['trader_id'] == self.id]

    def get_my_orders(self, orders):
        """filter full orders to get only mine.
        TODO: we won't need it if/when TS will send only my orders to me"""

        return [order for order in orders if order['trader_id'] == self.id]

    async def send_cancel_order_request(self, order_id: uuid.UUID):
        cancel_order_request = {
            "action": ActionType.CANCEL_ORDER.value,  # Assuming you have an ActionType Enum
            "trader_id": self.id,
            "order_id": order_id
        }

        response = await self.send_to_trading_system(cancel_order_request)
        # TODO: deal with response if needed (what if order is already cancelled? what is a part of transaction?
        #  what if order is not found? what if order is not yours?)
        logger.warning(f"Trader {self.id} sent cancel order request: {cancel_order_request}")

    async def find_and_cancel_order(self, price):
        """finds the order with the given price and cancels it"""
        for order in self.orders:
            if order['price'] == price:
                await self.send_cancel_order_request(order['id'])
                self.orders.remove(order)
                return

        logger.warning(f"Trader {self.id} tried to cancel order with price {price} but it was not found")
        logger.warning(f'Available prices are: {[order.get("price") for order in self.orders]}')

    async def run(self):
        try:
            while True:
                orders_to_do = self.generate_noise_orders()
                for order in orders_to_do:
                    if order['action_type'] == ActionType.POST_NEW_ORDER.value:
                        order_type_str = order['order_type']
                        order_type_value = OrderType[order_type_str.upper()]
                        await self.post_new_order(order['amount'], order['price'], order_type_value)
                    elif order['action_type'] == ActionType.CANCEL_ORDER.value:
                        await self.find_and_cancel_order(order['price'])

                await asyncio.sleep(0.5)  # LEt's post them every second. TODO: REMOVE THIS
        except Exception as e:
            # Handle the exception here
            logger.error(f"Exception in trader run: {e}")
            # Optionally re-raise the exception if you want it to be propagated
            raise

    def generate_noise_orders(self):
        # Convert the active orders to the book format understood by get_noise_rule

        book_format = convert_to_book_format(self.all_orders)
        # logger.critical(f"Book format: {list(book_format)}")
        #
        # # Convert the trader's state to the noise_state format
        noise_state = convert_to_noise_state(self.orders)  # Assuming you have this method
        #
        # # Get the noise signal
        signal_noise = get_signal_noise(signal_state=None, settings_noise=settings_noise)
        #
        # # Generate noise orders
        # noise_orders = get_noise_rule(book_format, signal_noise, noise_state, settings_noise, settings)
        # noise_orders = get_noise_rule(book_format, signal_noise, noise_state, settings_noise, settings)
        noise_orders = get_noise_rule_unif(book_format, signal_noise, noise_state, settings_noise, settings)
        converted_noise_orders = convert_to_trader_actions(noise_orders)

        return converted_noise_orders
