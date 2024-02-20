from .base_trader import BaseTrader
import asyncio
import random
import uuid
from structures import OrderType, TraderType
import logging
logger = logging.getLogger(__name__)
class NoiseTrader(BaseTrader):


    def __init__(self, activity_frequency: int, starting_price=None, step=None, number_of_steps=None ):
        super().__init__(trader_type=TraderType.NOISE)  # Assuming "NOISE" is a valid TraderType
        self.id=None
        self.activity_frequency = activity_frequency
        self.starting_price = starting_price
        self.step = step
        self.number_of_steps = number_of_steps
    async def warm_up(self, starting_price, step, number_of_steps, number_of_warmup_orders):
        # Initialize with warm-up specific parameters
        self.starting_price = starting_price
        self.step = step
        self.number_of_steps = number_of_steps

        for _ in range(number_of_warmup_orders):
            print('WE ARE IN WARM UP', _)
            await self.put_random_order()
    async def put_random_order(self):

        order_type = random.choice([OrderType.BID, OrderType.ASK])

        if self.starting_price and self.step and self.number_of_steps:
            price_step = random.randint(1, self.number_of_steps) * self.step
            if order_type == OrderType.BID:
                price = self.starting_price - price_step
            else:  # OrderType.ASK
                price = self.starting_price + price_step
        else:
            # Fallback to a random price if not in warm-up or if parameters are not set
            price = random.uniform(1, 100)

        amount = 1  # Assuming a standard amount for orders
        await self.post_new_order(amount, price, order_type)
        logger.critical(f"Posted {order_type.value} order at Price {price}, Amount {amount}")

    async def run(self):
        while not self._stop_requested.is_set():
            try:
                await self.put_random_order()
                await asyncio.sleep(self.activity_frequency)
            except asyncio.CancelledError:
                logger.info('Run method cancelled, performing cleanup of noise trader...')
                await self.clean_up()
                raise
            except Exception as e:
                logger.error(f"An error occurred in NoiseTrader run loop: {e}")
                break