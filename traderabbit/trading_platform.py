import aio_pika
import json
import uuid
from datetime import datetime
from traderabbit.utils import ack_message
from traderabbit.custom_logger import setup_custom_logger
from typing import List, Dict
from config import OrderStatus, OrderModel, OrderType, TransactionModel, LobsterEventType
import asyncio
from collections import defaultdict
from traderabbit.utils import (CustomEncoder,
                               create_lobster_message,
                               append_lobster_messages_to_csv,
                               convert_to_book_format,
                               append_order_books_to_csv,
                               append_combined_data_to_csv
                               )
from asyncio import Lock, Event

logger = setup_custom_logger(__name__)

"""bufferization:

 equalize human and algorithms. """
class TradingSystem:
    transactions = List[TransactionModel]
    all_orders = Dict[uuid.UUID, Dict]

    buffered_orders = Dict[uuid.UUID, Dict]

    @property
    def active_orders(self):
        return {k: v for k, v in self.all_orders.items() if v['status'] == OrderStatus.ACTIVE}

    def __init__(self, buffer_delay=5, max_buffer_releases=None):
        """
        buffer_delay: The delay in seconds before the Trading System processes the buffered orders.
        """
        # self.id = uuid.uuid4()
        self.max_buffer_releases = max_buffer_releases
        logger.critical(f"Max buffer releases: {self.max_buffer_releases}")
        self.buffer_release_count = 0
        self.buffer_release_time = None
        self.id = "1234"  # for testing purposes
        self.creation_time = datetime.utcnow()
        self.all_orders = {}
        self.buffered_orders = {}
        self.transactions = []
        self.broadcast_exchange_name = f'broadcast_{self.id}'
        self.queue_name = f'trading_system_queue_{self.id}'
        self.trader_exchange = None
        logger.info(f"Trading System created with UUID: {self.id}. Buffer delay is: {buffer_delay} seconds")
        self.connected_traders = {}
        self.buffer_delay = buffer_delay

        self.release_task = None
        self.lock = Lock()
        self.release_event = Event()

    async def initialize(self):
        self.connection = await aio_pika.connect_robust("amqp://localhost")
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
            print(f"An error occurred during cleanup: {e}")

    async def send_broadcast(self, message):
        exchange = await self.channel.get_exchange(self.broadcast_exchange_name)
        await exchange.publish(
            aio_pika.Message(body=json.dumps(message, cls=CustomEncoder).encode()),
            routing_key=''  # routing_key is typically ignored in FANOUT exchanges
        )

    async def send_message_to_trader(self, trader_id, message):
        await self.trader_exchange.publish(
            aio_pika.Message(body=json.dumps(message, cls=CustomEncoder).encode()),
            routing_key=f'trader_{trader_id}'
        )

    async def add_order_to_buffer(self, order):
        async with self.lock:
            logger.info(f"Adding order to buffer: {order}")
            trader_id = order['trader_id']
            self.buffered_orders[trader_id] = order
            # self.buffered_orders[trader_id] = order.model_dump()

            if len(self.buffered_orders) == len(self.connected_traders):
                self.release_event.set()

            if self.release_task is None:
                self.release_task = asyncio.create_task(self.release_buffered_orders())
            return dict(message="Order added to buffer", order=order,
                        # TODO: this is an ugly fix.... let's think how to deal with all that later
                        orders=self.list_active_orders + list(self.buffered_orders.values())
                        )

    @property
    def list_active_orders(self):
        return list(self.active_orders.values())
        
    def get_file_name(self):
        # todo: rename this to get_message_file_name
        """Returns file name for messages which is a trading platform id + datetime of creation with _ as spaces"""
        return f"{self.id}_{self.creation_time.strftime('%Y-%m-%d_%H-%M-%S')}"

    async def place_order(self, order_dict: Dict, trader_id: uuid.UUID):
        """ This one is called by handle_add_order, and is the one that actually places the order in the system.
        It adds automatically - we do all the validation (whether a trader allowed to place an order, etc) in the
        handle_add_order method.
        """
        order_id = order_dict['id']
        timestamp = order_dict['timestamp']
        order_dict.update({
            'status': OrderStatus.ACTIVE.value,
        })
        self.all_orders[order_id] = order_dict

        return order_dict

    def register_message(self, order, event_type):
        # Generate lobster message

        lobster_message = create_lobster_message(order, event_type=event_type,
                                         trader_type=self.connected_traders[order['trader_id']]['trader_type'],
                                         timestamp=order['timestamp'])

        # Generate book record using convert_to_book_format directly
        book_record = convert_to_book_format(self.active_orders.values())

        combined_row = {
            'message': lobster_message,
            'book_record': book_record,
            'original_timestamp': order['original_timestamp'].timestamp(),  # Include original timestamp
            'buffer_release_timestamp': order['timestamp'].timestamp() , # Include buffer release timestamp
            'buffer_release_count': self.buffer_release_count,
        }
        # Add parent_id to combined_row if it exists in the order
        if 'parent_id' in order:
            combined_row['parent_id'] = order['parent_id']
        return combined_row

    async def handle_transaction_for_order(self, order_id, combined_data):
        clear_result = await self.clear_orders()  # Attempt to clear orders and process transactions

        # Initialize the container for transactions
        if clear_result is None:
            clear_result = {'transactions': [], 'removed_active_orders': []}
        transactions = clear_result['transactions']
        removed_order_ids = clear_result['removed_active_orders']

        # Handle transactions logging
        for transaction in transactions:
            # Determine the most recent order (ask or bid) based on the timestamp
            ask_order = self.all_orders[transaction['ask_order_id']]
            bid_order = self.all_orders[transaction['bid_order_id']]
            most_recent_order = ask_order if ask_order['timestamp'] > bid_order['timestamp'] else bid_order

            # Create and append the transaction message
            combined_row = self.register_message(most_recent_order, LobsterEventType.EXECUTION_VISIBLE)
            combined_data.append(combined_row)

        # If no transaction was made, register the order
        if str(order_id) not in removed_order_ids:
            combined_row = self.register_message(self.all_orders[order_id], LobsterEventType.NEW_LIMIT_ORDER)
            combined_data.append(combined_row)

        return combined_data  # Return the updated combined_data

    async def release_buffered_orders(self):
        logger.info(f'total amount of buffered orders: {len(self.buffered_orders)}')
        """checks if the buffer is full and releases the orders if it is."""
        sleep_task = asyncio.create_task(asyncio.sleep(self.buffer_delay))
        release_event_task = asyncio.create_task(self.release_event.wait())
        """d"""

        await asyncio.wait([sleep_task,
                            release_event_task
                            ], return_when=asyncio.FIRST_COMPLETED)

        async with self.lock:
            logger.info(f'we start releasing orders. total amount to release: {len(self.buffered_orders)}')

            self.buffer_release_time = datetime.utcnow()
            logger.info(f"Buffer release time: {self.buffer_release_time.timestamp()}")
            combined_data = []
            for trader_id, order_dict in self.buffered_orders.items():
                # lets create a temp list where we'll put ids of single orders OR splitted orders if amount>1
                temp_order_ids = []
                # Set the timestamp for the order
                order_dict['original_timestamp'] = order_dict['timestamp']
                order_dict['timestamp'] = self.buffer_release_time

                order_amount = order_dict['amount']
                """d"""
                if order_amount > 1:
                    """d"""
                    for _ in range(order_amount):
                        split_order = order_dict.copy()
                        split_order['parent_id'] = order_dict['id']
                        split_order['id'] = uuid.uuid4()  # Generate a new UUID for each split order
                        # Add the split order to the temp list
                        temp_order_ids.append(split_order['id'])
                        split_order['amount'] = 1
                        logger.info(f'Placing split order: {split_order}')
                        await self.place_order(split_order, trader_id)
                        combined_data = await self.handle_transaction_for_order(split_order['id'], combined_data)
                else:
                    # Add the order to the temp list
                    temp_order_ids.append(order_dict['id'])
                    logger.info(f'Placing order: {order_dict}')
                    await self.place_order(order_dict, trader_id)
                    combined_data = await self.handle_transaction_for_order(order_dict['id'], combined_data)


            logger.info(f"Total of {len(self.buffered_orders)} orders released from buffer")
            # Clear orders and get transactions and ids of removed orders
            if combined_data:
                await append_combined_data_to_csv(combined_data, self.get_file_name())

            # Clear the buffered orders
            self.buffered_orders.clear()

            self.release_task = None  # Reset the task so it can be recreated
            self.release_event.clear()  # Reset the event
            # TODO: let's think about the depth of the order book to send; and also do we need all transactions??
            await self.send_broadcast(message=dict(message="Buffer released",
                                                   # TODO: this is an ugly fix.... let's think how to deal with all that later
                                                   orders=self.list_active_orders + list(self.buffered_orders.values())
                                                   ))

            # Increment the buffer release count

            self.buffer_release_count += 1
            logger.critical(f"Buffer release count: {self.buffer_release_count}")

    def check_counters(self):

        """ Checks if the buffer release count exceeds the limit. """
        logger.info(f'self.buffer_release_count {self.buffer_release_count}')
        if self.max_buffer_releases is not None and self.buffer_release_count >= self.max_buffer_releases:
            return True
        return False

    async def clear_orders(self):
        res = {'transactions': [], 'removed_active_orders': []}
        logger.info(f'Total amount of active orders: {len(self.active_orders)}')
        # Separate active orders into asks and bids
        asks = [order for order in self.active_orders.values() if order['order_type'] == OrderType.ASK.value]
        bids = [order for order in self.active_orders.values() if order['order_type'] == OrderType.BID.value]

        # Sort by price (lowest first for asks), and then by timestamp (oldest first - FIFO)
        # TODO: remember that this is FIFO. We need to adjust the rule (or make it adjustable in the config) if we want
        asks.sort(key=lambda x: (x['price'], x['timestamp']))
        # Sort by price (highest first for bids), and then by timestamp (oldest first)
        bids.sort(key=lambda x: (-x['price'], x['timestamp']))

        # Calculate the spread
        if asks and bids:
            lowest_ask = asks[0]['price']
            highest_bid = bids[0]['price']
            spread = lowest_ask - highest_bid
        else:
            logger.info("No overlapping orders.")
            return res

        # Check if any transactions are possible
        if spread > 0:
            logger.info(
                f"No overlapping orders. Spread is positive: {spread}. Lowest ask: {lowest_ask}, highest bid: {highest_bid}")

            return res

        logger.info(f"Spread: {spread}")
        # Filter the bids and asks that could be involved in a transaction
        viable_asks = [ask for ask in asks if ask['price'] <= highest_bid]
        viable_bids = [bid for bid in bids if bid['price'] >= lowest_ask]
        logger.info(f'Viable asks: {len(viable_asks)}')
        logger.info(f'Viable bids: {len(viable_bids)}')
        to_remove = []
        transactions = []

        # Create transactions
        while viable_asks and viable_bids:
            ask = viable_asks.pop(0)
            bid = viable_bids.pop(0)

            # Change the status to 'EXECUTED' in the all_orders dictionary
            self.all_orders[ask['id']]['status'] = OrderStatus.EXECUTED.value
            self.all_orders[bid['id']]['status'] = OrderStatus.EXECUTED.value
            timestamp = datetime.utcnow()

            # Create a transaction
            transaction_price = (ask['price'] + bid['price']) / 2  # Mid-price
            transaction = TransactionModel(
                id=uuid.uuid4(),
                bid_order_id=bid['id'],
                ask_order_id=ask['id'],
                timestamp=timestamp,
                price=transaction_price
            )
            logger.info(f"Transaction created: {transaction.model_dump()}")
            transactions.append(transaction.model_dump())

            to_remove.extend([str(ask['id']), str(bid['id'])])

        # Add transactions to self.transactions
        self.transactions.extend(transactions)

        # Log the number of cleared and transacted orders
        logger.info(f"Cleared {len(to_remove)} orders.")
        logger.info(f"Created {len(transactions)} transactions.")
        res['removed_active_orders'] = to_remove
        res['transactions'] = transactions
        return res

    async def handle_add_order(self, data: dict):
        # TODO: Validate the order
        trader_id = data.get('trader_id')
        clean_order = {
            'id': uuid.uuid4(),
            'status': OrderStatus.BUFFERED.value,
            'for_execution_only': data.get('for_execution_only', False),
            'amount': data.get('amount'),
            'price': data.get('price'),
            'order_type': data.get('order_type'),
            'timestamp': datetime.utcnow(),
            'session_id': self.id,
            'trader_id': trader_id
        }
        resp = await self.add_order_to_buffer(clean_order)
        if resp:
            logger.info(f'Total active orders: {len(self.active_orders)}')

        return dict(respond=True, **resp)

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
            timestamp = datetime.utcnow()
            self.all_orders[order_id]['status'] = OrderStatus.CANCELLED.value

            # Create a combined row for the cancel event
            combined_row = self.register_message(existing_order,  LobsterEventType.CANCELLATION_TOTAL)

            # Append the combined row to the CSV
            await append_combined_data_to_csv([combined_row], self.get_file_name())

            logger.info(f"Order {order_id} has been canceled for trader {trader_id}.")

            # Broadcast the cancellation, implementation may vary based on your system's logic
            await self.broadcast_order_cancellation(trader_id)

            return {"status": "cancel success", "order": order_id, "respond": True}

    async def broadcast_order_cancellation(self, trader_id):
        # Implementation for broadcasting order cancellation
        # Note: Make sure self.list_active_orders and self.buffered_orders reflect the current state after cancellation
        await self.send_broadcast(message=dict(message="Order is cancelled",
                                               orders=self.list_active_orders + list(self.buffered_orders.values())))

    async def handle_update_book_status(self, order):
        """This one returns the most recent book to the trader who requested it.
        TODO: now we just stupidly pass all active orders. We need to filter them based on the trader_id, perhaps
        we'll generate a proper book (with prices-levels) here. but not now.
        """
        trader_id = order.get('trader_id')
        if trader_id:
            return {"status": "success", "respond": True, "orders": self.active_orders.values()}

    async def handle_register_me(self, msg_body):
        trader_id = msg_body.get('trader_id')
        self.connected_traders[trader_id] = {'trader_type': msg_body.get('trader_type'), }
        logger.info(f"Trader {trader_id} connected.")
        logger.info(f"Total connected traders: {len(self.connected_traders)}")

    @ack_message
    async def on_individual_message(self, message):

        incoming_message = json.loads(message.body.decode())
        logger.info(f"TS {self.id} received message: {incoming_message}")
        action = incoming_message.pop('action', None)
        trader_id = incoming_message.get('trader_id', None)  # Assuming the trader_id is part of the message

        handler_method = getattr(self, f"handle_{action}", None)
        if action:
            if handler_method:
                result = await handler_method(incoming_message)
                if result and result.pop('respond', None) and trader_id:
                    await self.send_message_to_trader(trader_id, result)

            else:
                logger.warning(f"No handler method found for action: {action}")
        else:
            logger.warning(f"No action found in message: {incoming_message}")

    async def run(self):
        """ Keeps system active. Stops if the buffer release limit is reached. """
        try:
            while True:
                logger.info('Checking counters...')
                if self.check_counters():
                    logger.critical('Counter limit reached, stopping...')
                    break
                await asyncio.sleep(1)
            logger.critical('Exited the run loop.')
        except Exception as e:
            # Handle the exception here
            logger.error(f"Exception in trading session run: {e}")
            # Optionally re-raise the exception if you want it to be propagated
            raise