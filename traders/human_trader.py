from base_trader import BaseTrader

import uuid
import asyncio
import random
import time
import json
import pandas as pd
from structures import TraderCreationData, TraderType
import logging

logger = logging.getLogger(__name__)


class HumanTrader(BaseTrader):
    websocket = None

    def __init__(self, trader_data: TraderCreationData):
        super().__init__(trader_type=TraderType.HUMAN)


    async def send_message_to_client(self, type, **kwargs):
        return await self.websocket.send_json(
            {
                'type': type,
                **kwargs
            }
        )

    async def on_message_from_client(self, message):
        """
        process  incoming messages from human client
        """
        try:
            json_message = json.loads(message)
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
        order_type = data.get('type')
        price = data.get('price')
        amount = data.get('amount')
        await self.post_new_order(amount, price, order_type)

    async def handle_cancel_order(self, data):
        order_uuid = data.get('id')
        # Check if the order UUID exists in the DataFrame
        if order_uuid in self.orders_df['uuid'].values:
            await self.send_cancel_order_request(order_uuid)
        else:
            # Handle the case where the order UUID does not exist
            logger.warning(f"Order with UUID {order_uuid} not found.")


