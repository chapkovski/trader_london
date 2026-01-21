import asyncio
import random
from structures import OrderType, TraderType
from main_platform.custom_logger import setup_custom_logger
from .base_trader import BaseTrader

logger = setup_custom_logger(__name__)


class NoiseTrader(BaseTrader):
    def __init__(
        self,
        activity_frequency: float,
        order_amount: int,
        settings: dict,
    ):
        """
        initializes the noise trader with simple, configurable behavior.
        """
        super().__init__(trader_type=TraderType.NOISE)

        self.activity_frequency = activity_frequency
        self.order_amount = order_amount
        self.settings = settings or {}
        self.initial_price = self.settings.get("initial_price", 1000)
        self.price_step = self.settings.get("price_step", 1)
        self.cancel_probability = self.settings.get("cancel_probability", 0.25)
        self.max_price_deviation = self.settings.get("max_price_deviation", 5)

    def cooling_interval(self, target: float) -> float:
        """
        adjusts cooling interval using a random process.
        """
        if target <= 0:
            return 0
        return random.expovariate(1 / target)

    async def act(self):
        """
        generates action based on active orders in the market.
        """
        if not self.active_orders:
            # Kickstart the book with a single order when the market is empty.
            await self.post_new_order(
                self.order_amount,
                self.initial_price,
                random.choice([OrderType.ASK, OrderType.BID]),
            )
            return

        if self.orders and random.random() < self.cancel_probability:
            # Occasionally cancel one of our own orders to keep churn.
            order_id = random.choice(self.orders)["id"]
            await self.send_cancel_order_request(order_id)
            logger.info("CANCELLED ORDER %s", order_id)
            return

        # Otherwise place a new order near the mid price.
        order_type = self._choose_order_type()
        _, _, mid_price = self._get_market_prices()
        price = self._pick_price(mid_price)
        await self.post_new_order(self.order_amount, price, order_type)
        logger.info("POSTED %s AT %s AMOUNT %s", order_type, price, self.order_amount)

    def _choose_order_type(self) -> OrderType:
        bid_count = sum(1 for order in self.active_orders if order["order_type"] == OrderType.BID)
        ask_count = sum(1 for order in self.active_orders if order["order_type"] == OrderType.ASK)
        if bid_count == 0 and ask_count > 0:
            return OrderType.BID
        if ask_count == 0 and bid_count > 0:
            return OrderType.ASK
        return random.choice([OrderType.ASK, OrderType.BID])

    def _get_market_prices(self):
        bids = self.order_book.get("bids", []) if self.order_book else []
        asks = self.order_book.get("asks", []) if self.order_book else []
        best_bid = bids[0]["x"] if bids else None
        best_ask = asks[0]["x"] if asks else None
        if best_bid is not None and best_ask is not None:
            mid_price = (best_bid + best_ask) / 2
        elif best_bid is not None:
            mid_price = best_bid
        elif best_ask is not None:
            mid_price = best_ask
        else:
            mid_price = self.initial_price
        return best_bid, best_ask, mid_price

    def _pick_price(self, mid_price):
        offset = random.randint(-self.max_price_deviation, self.max_price_deviation)
        price = mid_price + offset * self.price_step
        return max(price, self.price_step)

    async def warm_up(self, number_of_warmup_orders: int):
        """
        places warmup orders to poulate order book.
        """
        for _ in range(number_of_warmup_orders):
            await self.act()

    async def run(self):
        """
        trades at cooling intervals.
        """
        while not self._stop_requested.is_set():
            try:
                await self.act()

                await asyncio.sleep(
                    self.cooling_interval(target=self.activity_frequency)
                )

            except asyncio.CancelledError:
                logger.info(
                    "Run method cancelled, performing cleanup of %s...",
                    self.trader_type,
                )
                await self.clean_up()
                raise
            except Exception as e:
                logger.error("An error occurred in NoiseTrader run loop: %s", e)
                break
