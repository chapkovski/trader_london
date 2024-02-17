import uuid
import asyncio
import random
import time
import json
import pandas as pd
from structs import TraderCreationData
import logging

logger = logging.getLogger(__name__)


class HumanTrader:
    websocket = None

    def __init__(self, trader_data: TraderCreationData):
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

    def calculate_spread(self):
        # Use the aggregated order book for spread calculation
        aggregated_order_book = self.order_book

        # Ensure there are both bids and asks
        if not aggregated_order_book['bids'] or not aggregated_order_book['asks']:
            return None

        highest_bid = max(aggregated_order_book['bids'], key=lambda x: x['x'])['x']
        lowest_ask = min(aggregated_order_book['asks'], key=lambda x: x['x'])['x']

        # Spread is the difference between the lowest ask and the highest bid
        return lowest_ask - highest_bid

    @property
    def active_orders(self):
        # Filter and return active orders
        return self.orders_df[self.orders_df['status'] == 'active']

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
    async def send_message(self, type, **kwargs):
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

    async def run(self):
        n = self.noise_trader_update_freq
        while True:
            print('PERIODIC UPDATE')
            self.generate_order()
            self.execute_orders()
            await self.send_message('update')
            await asyncio.sleep(n)

    def generate_order(self):
        # Generate a new order
        new_order_price = self.calculate_new_order_price()
        order_type = random.choice(['bid', 'ask'])

        new_order = {
            'uuid': str(uuid.uuid4()),
            'timestamp': time.time(),
            'type': order_type,
            'price': new_order_price,
            'quantity': 1,
            'status': 'active',
            'owner': 'system'  # Or any appropriate identifier
        }

        new_order_df = pd.DataFrame([new_order])
        self.orders_df = pd.concat([self.orders_df, new_order_df], ignore_index=True)

    def execute_orders(self):
        # Filter active bids and asks
        active_bids = self.active_orders[self.active_orders['type'] == 'bid']
        active_asks = self.active_orders[self.active_orders['type'] == 'ask']

        # Sort bids and asks by price and timestamp
        active_bids = active_bids.sort_values(by=['price', 'timestamp'], ascending=[False, True])
        active_asks = active_asks.sort_values(by=['price', 'timestamp'], ascending=[True, True])

        # Execute orders
        while not active_bids.empty and not active_asks.empty and active_bids.iloc[0]['price'] >= active_asks.iloc[0][
            'price']:
            # Determine the price of the earliest order
            executed_price = active_bids.iloc[0]['price'] if active_bids.iloc[0]['timestamp'] < active_asks.iloc[0][
                'timestamp'] else active_asks.iloc[0]['price']
            self.transaction_history.append({'price': executed_price, 'timestamp': time.time()})

            # Update the quantities and status in the DataFrame
            bid_index = active_bids.index[0]
            ask_index = active_asks.index[0]

            # Check if the human trader's bid or ask order is executed
            if bid_index in self.own_orders.index:
                self.shares += self.orders_df.at[bid_index, 'quantity']
                self.cash -= executed_price * self.orders_df.at[bid_index, 'quantity']

            if ask_index in self.own_orders.index:
                self.shares -= self.orders_df.at[ask_index, 'quantity']
                self.cash += executed_price * self.orders_df.at[ask_index, 'quantity']

            # Decrease the quantity and update status
            self.orders_df.at[bid_index, 'quantity'] -= 1
            self.orders_df.at[ask_index, 'quantity'] -= 1

            if self.orders_df.at[bid_index, 'quantity'] <= 0:
                self.orders_df.at[bid_index, 'status'] = 'executed'
            if self.orders_df.at[ask_index, 'quantity'] <= 0:
                self.orders_df.at[ask_index, 'status'] = 'executed'

            # Refresh active bids and asks
            active_bids = self.active_orders[self.active_orders['type'] == 'bid']
            active_asks = self.active_orders[self.active_orders['type'] == 'ask']

    def calculate_new_order_price(self):
        # Implement logic to calculate the price of the new order
        return random.randint(9500, 10500)  # Placeholder logic

    def start_updates(self, websocket):
        self.websocket = websocket
        self.update_task = asyncio.create_task(self.run())
        self.update_task.add_done_callback(self.task_done_callback)

    def task_done_callback(self, task):
        try:
            task.result()
        except Exception as e:
            print(f"Exception in task: {e}")
            raise e

    def stop_updates(self):
        if self.update_task:
            self.update_task.cancel()

    async def handle_incoming_message(self, message):
        """
        Handle incoming messages to add new orders and check for executions.
        """
        try:
            json_message = json.loads(message)
            action_type = json_message.get('type')
            data = json_message.get('data')
            # let's check if we have a function that can deal with this action type

            print('*' * 50)
            print(f"Received message: {json_message}")
            handler = getattr(self, f'handle_{action_type}', None)
            if handler:
                await handler(data)
                self.execute_orders()
                await self.send_message('update')
                # todo: not the best solution above but let's think later about it
            elif action_type in ['aggressiveAsk', 'passiveAsk', 'aggressiveBid', 'passiveBid']:
                print('are we gonna process?')
                self.process_order(action_type)
                self.execute_orders()
                await self.send_message('update')
            elif action_type == 'cancel':
                order_uuid = data.get('uuid')
                print(f'Cancelling order: {order_uuid}')
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

    async def cancel_order(self, order_uuid):
        # Check if the order UUID exists in the DataFrame
        if order_uuid in self.orders_df['uuid'].values:
            # Set the status of the order to 'cancelled'
            self.orders_df.loc[self.orders_df['uuid'] == order_uuid, 'status'] = 'cancelled'
            await self.send_message('update')
        else:
            # Handle the case where the order UUID does not exist
            print(f"Order with UUID {order_uuid} not found.")

    def process_order(self, action_type):
        # Get the current order book
        current_order_book = self.order_book
        price = None

        if action_type == 'aggressiveAsk':
            logger.info("# Aggressive Ask: Put an ask at the best bid level for immediate execution")
            if current_order_book['bids']:
                price = current_order_book['bids'][0]['x']
        elif action_type == 'passiveAsk':
            logger.info("# Passive Ask: Put an ask at the existing best ask level")
            if current_order_book['asks']:
                price = current_order_book['asks'][0]['x']
            else:
                price = self.default_ask_price()
        elif action_type == 'aggressiveBid':
            logger.info("# Aggressive Bid: Put a bid at the best ask level for immediate execution")
            if current_order_book['asks']:
                price = current_order_book['asks'][0]['x']
        elif action_type == 'passiveBid':
            logger.info("# Passive Bid: Put a bid at the existing best bid level")
            if current_order_book['bids']:
                price = current_order_book['bids'][0]['x']
            else:
                price = self.default_bid_price()

        if price is not None:
            order_type = 'ask' if 'Ask' in action_type else 'bid'
            self.add_order(order_type, price)

    def default_ask_price(self):
        # Define a default ask price if there are no asks in the order book
        return 10100  # Example value, adjust as needed

    def default_bid_price(self):
        # Define a default bid price if there are no bids in the order book
        return 9500  # Example value, adjust as needed

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

        # Convert new_order to a DataFrame and concatenate with self.orders_df
        new_order_df = pd.DataFrame([new_order])
        self.orders_df = pd.concat([self.orders_df, new_order_df], ignore_index=True)