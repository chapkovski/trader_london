import asyncio
import aio_pika
import json
import uuid
from config.structures import OrderType, ActionType, TraderType, params
from traderabbit.utils import ack_message, convert_to_noise_state, convert_to_book_format, convert_to_trader_actions
from traderabbit.custom_logger import setup_custom_logger
from traders.noise_trader import get_signal_noise, settings_noise, settings, get_noise_rule_unif

logger = setup_custom_logger(__name__)

class Trader:
    orders = []
    all_orders = []
    transactions = []

    def __init__(self, trader_type: TraderType):
        self.trader_type = trader_type.value
        self.id = str(uuid.uuid4())
        print(f"{trader_type} Trader created with UUID: {self.id}")
        self.connection = None
        self.channel = None
        self.trading_session_uuid = None
        self.trader_queue_name = f'trader_{self.id}'  # unique queue name based on Trader's UUID
        logger.info(f"{trader_type} Trader created with UUID: {self.id}")
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
            pass
            # print(f"An error occurred during Trader cleanup: {e}")

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
            "action": ActionType.CANCEL_ORDER.value, 
            "trader_id": self.id,
            "order_id": order_id
        }

        response = await self.send_to_trading_system(cancel_order_request)
        # TODO: deal with response if needed (what if order is already cancelled? what is a part of transaction?
        #  what if order is not found? what if order is not yours?)
        logger.warning(f"Trader {self.id} sent cancel order request: {cancel_order_request}")


    async def run(self):
        raise NotImplementedError("Method only implmemented in subclasses. This is baseclass.")

class NoiseTrader(Trader):

    def __init__(self, trader_type: TraderType):
        super().__init__(trader_type)

    async def find_and_cancel_order(self, price):
        """finds the order with the given price and cancels it"""
        for order in self.orders:
            if order['price'] == price:
                await self.send_cancel_order_request(order['id'])
                self.orders.remove(order)
                return

        logger.warning(f"Trader {self.id} tried to cancel order with price {price} but it was not found")
        logger.warning(f'Available prices are: {[order.get("price") for order in self.orders]}')

    def generate_noise_orders(self):
        """generates noise orders based on the current book state and noise state"""
        book_format = convert_to_book_format(self.all_orders)
        noise_state = convert_to_noise_state(self.orders)
        signal_noise = get_signal_noise(signal_state=None, settings_noise=settings_noise)
        noise_orders = get_noise_rule_unif(book_format, signal_noise, noise_state, settings_noise, settings)
        return convert_to_trader_actions(noise_orders)

    async def run(self):
        """simulation step for the noise trader"""
        try:
            while True:
                for order in self.generate_noise_orders():
                    action_type = order['action_type']
                    if action_type == ActionType.POST_NEW_ORDER.value:
                        await self.post_new_order(order['amount'], order['price'], OrderType[order['order_type'].upper()])
                    elif action_type == ActionType.CANCEL_ORDER.value:
                        await self.find_and_cancel_order(order['price'])

                await asyncio.sleep(params.trader.post_every_x_second)  # Consider making this a variable or configuration if possible
        except Exception as e:
            logger.error(f"Exception in trader run: {e}")
            raise
        

from queue import Queue
import asyncio

class HumanTrader(Trader):
    def __init__(self, trader_type: TraderType, trader_id: str, loop=None):
        super().__init__(trader_type)
        self.id = trader_id  # Override the UUID with a provided trader ID
        self.message_queue = asyncio.Queue()
        self.loop = loop or asyncio.get_event_loop()

    async def place_order(self, order_data: dict):
        amount = order_data['amount']
        price = order_data['price']
        # Translate the order_type string to the correct OrderType enum
        order_type_str = order_data['order_type'].upper()
        print(f'amount: {amount}, price: {price}, order_type: {order_type_str}')
        if order_type_str == 'BUY':
            order_type = OrderType.BID  # Assuming 'BUY' corresponds to a bid
        elif order_type_str == 'SELL':
            order_type = OrderType.ASK  # Assuming 'SELL' corresponds to an ask
        else:
            raise ValueError(f"Unknown order type: {order_type_str}")
        await self.post_new_order(amount, price, order_type)

    async def cancel_order(self, order_id):
        # Convert order_id to UUID if it's not already one
        if not isinstance(order_id, uuid.UUID):
            order_id = uuid.UUID(order_id)
        await self.send_cancel_order_request(order_id)
        logger.info(f"Cancel order request sent for order_id: {order_id}")

    async def run(self):
        logger.info(f"Running HumanTrader ID {self.id}...")

        try:
            while True:
                if not self.message_queue.empty():
                    message = await self.message_queue.get()
                    action = message.get("action")
                    if action == "cancel_order":
                        # Call the method to handle cancel order request
                        await self.cancel_order(message["order_id"])
                    elif action == "add_order":
                        # Handle add order request
                        await self.place_order(message)
                    self.message_queue.task_done()
                
                await asyncio.sleep(params.trader.post_every_x_second)
        except Exception as e:
            logger.error(f"Exception in HumanTrader run: {e}")