from .base_trader import BaseTrader

from pprint import pprint
import json

from structures import TraderType, OrderType
import logging

logger = logging.getLogger(__name__)


class HumanTrader(BaseTrader):
    websocket = None
    inventory = {'shares': 0, 'cash': 1000} # TODO.PHILIPP. WRite something sensible here. placeholder for now.
    def __init__(self):
        super().__init__(trader_type=TraderType.HUMAN)

    async def post_processing_server_message(self, json_message):
        print('BBBB'*100)
        pprint(json_message)
        print('BBBB'*100)
        message_type = json_message.pop('type')
        if message_type:
            await self.send_message_to_client(message_type, **json_message)

    def connect_to_socket(self, websocket):
        self.websocket = websocket

    async def send_message_to_client(self, message_type, **kwargs):

        trader_orders = self.orders or []

        kwargs['trader_orders'] = trader_orders
        print('$'*100)
        pprint(kwargs)
        print('$' * 100)
        return await self.websocket.send_json(
            {
                'type': message_type,
                'inventory': self.inventory,
                **kwargs
            }
        )

    async def on_message_from_client(self, message):
        """
        process  incoming messages from human client
        """
        try:
            json_message = json.loads(message)
            print(json_message)
            print('AAAA'*100)
            action_type = json_message.get('type')
            data = json_message.get('data')
            handler = getattr(self, f'handle_{action_type}', None)
            if handler:
                await handler(data)
            else:
                print(f"Invalid message format: {message}")
        except json.JSONDecodeError:
            print(f"Error decoding message: {message}")

    async def handle_add_order(self, data):
        order_type = data.get('type') # TODO: Philipp. This is a string. We need to convert it to an enum.
        # TODO. Philipp. We may rewrite a client so it will send us an enum instead of a string.
        if order_type == 'bid':
            order_type = OrderType.BID
        else:
            order_type = OrderType.ASK

        price = data.get('price')
        amount = data.get('amount')
        await self.post_new_order(amount, price, order_type)

    async def handle_cancel_order(self, data):
        order_uuid = data.get('id')
        # Check if the order UUID exists in the DataFrame
        if order_uuid in self.orders.keys():
            await self.send_cancel_order_request(order_uuid)
        else:
            # Handle the case where the order UUID does not exist
            logger.warning(f"Order with UUID {order_uuid} not found.")
