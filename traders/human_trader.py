from base_trader import BaseTrader


import uuid
import asyncio
import random
import time
import json
import pandas as pd
from structures import TraderCreationData
import logging

logger = logging.getLogger(__name__)


class HumanTrader(BaseTrader):
    websocket = None

    def __init__(self, trader_data: TraderCreationData):
        super().__init__()
        self.uuid = str(uuid.uuid4())
        self.update_task = None
        self.orders_df = pd.DataFrame(columns=['uuid', 'timestamp', 'type', 'price', 'quantity', 'status', 'owner'])
        self.generate_initial_order_book()
        self.transaction_history = self.generate_initial_history()

        # Use parameters from TraderCreationData
        self.shares = trader_data.initial_shares
        self.cash = trader_data.initial_cash
        self.max_short_shares = trader_data.max_short_shares
        self.max_short_cash = trader_data.max_short_cash
        self.trading_day_duration = trader_data.trading_day_duration
        self.max_active_orders = trader_data.max_active_orders
        self.noise_trader_update_freq = trader_data.noise_trader_update_freq

    @property
    def own_orders(self):
        # Filter and return orders that belong to the human trader
        return self.orders_df[self.orders_df['owner'] == 'human']



    @property
    def order_book(self):
        # Filter for active bids and asks
        active_bids = self.active_orders[(self.active_orders['type'] == 'bid')]
        active_asks = self.active_orders[(self.active_orders['type'] == 'ask')]

        # Initialize empty order book
        order_book = {'bids': [], 'asks': []}

        # Aggregate and format bids if there are any
        if not active_bids.empty:
            bids_grouped = active_bids.groupby('price').quantity.sum().reset_index().sort_values(by='price',
                                                                                                 ascending=False)
            order_book['bids'] = bids_grouped.rename(columns={'price': 'x', 'quantity': 'y'}).to_dict('records')

        # Aggregate and format asks if there are any
        if not active_asks.empty:
            asks_grouped = active_asks.groupby('price').quantity.sum().reset_index().sort_values(by='price')
            order_book['asks'] = asks_grouped.rename(columns={'price': 'x', 'quantity': 'y'}).to_dict('records')

        return order_book

    def generate_initial_order_book(self):
        # Number of initial orders on each side
        num_orders = 10

        # Lists to hold bid and ask orders
        bid_orders = []
        ask_orders = []

        # Generate initial bid orders
        for _ in range(num_orders):
            bid_orders.append({
                'uuid': str(uuid.uuid4()),
                'timestamp': time.time(),
                'type': 'bid',
                'price': random.randint(9500, 10000),
                'quantity': 1,
                'status': 'active',
                'owner': 'system'  # or another appropriate identifier
            })

        # Generate initial ask orders
        for _ in range(num_orders):
            ask_orders.append({
                'uuid': str(uuid.uuid4()),
                'timestamp': time.time(),
                'type': 'ask',
                'price': random.randint(10000, 10500),
                'quantity': 1,
                'status': 'active',
                'owner': 'system'
            })

        # Concatenate the new orders to the orders DataFrame
        new_orders = pd.DataFrame(bid_orders + ask_orders)
        self.orders_df = pd.concat([self.orders_df, new_orders], ignore_index=True)

    def generate_initial_history(self, interval=10, num_entries=10):
        # Get the current time
        current_time = time.time()

        # Generate history with prices at different timestamps
        history = []
        for i in range(num_entries):
            price = random.randint(9500, 10500)
            # Subtracting from the current time as we go back in the loop
            timestamp = current_time - (num_entries - 1 - i) * interval
            history.append({'price': price, 'timestamp': timestamp})

        return history

    # let's write a general method for sending updates to the client which will also automatically injects
    # the order book and transaction history into the message and also current spread and inventory situation
    # input: additional mesages that will be added to the dict
    # output: response of await websocket.send_json
    # the only required input field is type
    async def send_message_to_client(self, type, **kwargs):
        spread = self.calculate_spread()
        inventory = self.calculate_inventory()

        # Get the current price from the last transaction in the history
        current_price = self.transaction_history[-1]['price'] if self.transaction_history else None

        # Convert own_orders DataFrame to a list of dictionaries for JSON serialization
        trader_orders = self.own_orders.to_dict('records')

        return await self.websocket.send_json(
            {
                'type': type,
                'order_book': self.order_book,
                'history': self.transaction_history,
                'spread': spread,
                'inventory': inventory,
                'current_price': current_price,
                'trader_orders': trader_orders,
                **kwargs
            }
        )

    def calculate_inventory(self):
        # Return the actual inventory
        return {'shares': self.shares, 'cash': self.cash}

    async def handle_incoming_message(self, message):
        """
        Handle incoming messages from human client
        """
        try:
            json_message = json.loads(message)
            action_type = json_message.get('type')
            data = json_message.get('data')
            handler = getattr(self, f'handle_{action_type}', None)
            if handler:
                await handler(data)
                # todo: not the best solution above but let's think later about it
                await self.send_message_to_client('update')

            elif action_type in ['aggressiveAsk', 'passiveAsk', 'aggressiveBid', 'passiveBid']:
                print('are we gonna process?')
                self.process_order(action_type)
                self.execute_orders()
                await self.send_message_to_client('update')
            elif action_type == 'cancel':
                order_uuid = data.get('uuid')
                await self.cancel_order(order_uuid)
            else:
                print(f"Invalid message format: {message}")
        except json.JSONDecodeError:
            print(f"Error decoding message: {message}")

    # let's deal with the follwing incoming data to add orders:
    # {"type":"add_order","data":{"type":"ask","price":10048,"quantity":1}}
    async def handle_add_order(self, data):
        order_type = data.get('type')
        price = data.get('price')
        self.add_order(order_type, price)

    async def handle_cancel_order(self, data):
        order_uuid = data.get('id')
        # Check if the order UUID exists in the DataFrame
        if order_uuid in self.orders_df['uuid'].values:
            await self.send_cancel_order_request(order_uuid)
        else:
            # Handle the case where the order UUID does not exist
            logger.warning(f"Order with UUID {order_uuid} not found.")






    def add_order(self, order_type, price, owner='human'):

        new_order = {
            'uuid': str(uuid.uuid4()),
            'timestamp': time.time(),
            'type': order_type,
            'price': price,
            'quantity': 1,
            'status': 'active',
            'owner': owner
        }
