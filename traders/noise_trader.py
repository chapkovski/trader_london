from .base_trader import BaseTrader
import asyncio
import random
import uuid
from structures import OrderType, TraderType
import logging
logger = logging.getLogger(__name__)
class NoiseTrader(BaseTrader):
    def __init__(self, activity_frequency: int):
        super().__init__(trader_type=TraderType.NOISE)  # Assuming "NOISE" is a valid TraderType
        self.activity_frequency = activity_frequency

    async def run(self):
        while True:
            try:
                order_type = random.choice([OrderType.BID, OrderType.ASK])
                price = random.uniform(1, 100)
                amount = 1
                # Post the order
                await self.post_new_order(amount, price, order_type)
                logger.info(f"Posted {order_type.value} order: Price {price}, Amount {amount}")

                await asyncio.sleep(self.activity_frequency)
            except Exception as e:
                logger.error(f"An error occurred in NoiseTrader run loop: {e}")
                break
