import asyncio
import math
import random
from structures import OrderType, TraderType
from main_platform.custom_logger import setup_custom_logger
from .base_trader import BaseTrader

logger = setup_custom_logger(__name__)


class NoiseTrader(BaseTrader):
    def __init__(
        self,
        max_orders_per_minute: int,
        initial_midpoint: float = 100,
        initial_spread: float = 10,
    ):
        """
        Single noise trader that submits random order types against the human book.
        """
        super().__init__(trader_type=TraderType.NOISE)
        self.max_orders_per_minute = max(0, int(max_orders_per_minute or 0))
        self.initial_midpoint = float(initial_midpoint)
        self.initial_spread = max(0.0001, float(initial_spread or 10))
        self._initial_orders_seeded = False

    @staticmethod
    def _positive_price(price):
        return max(1, float(price))

    def _quotes_around(self, anchor_price: float):
        half_spread = self.initial_spread / 2.0
        bid_price = self._positive_price(anchor_price - half_spread)
        ask_price = self._positive_price(anchor_price + half_spread)
        if ask_price <= bid_price:
            ask_price = bid_price + self.initial_spread
        return bid_price, ask_price

    def _reference_price(self):
        if self.last_transaction_price is not None and self.last_transaction_price > 0:
            return float(self.last_transaction_price)
        return self.initial_midpoint

    async def seed_initial_orders(self):
        """
        Seed initial bid/ask once so market has a starting spread.
        """
        if self._initial_orders_seeded:
            return
        bid_price, ask_price = self._quotes_around(self.initial_midpoint)
        await self.post_new_order(1, bid_price, OrderType.BID)
        await self.post_new_order(1, ask_price, OrderType.ASK)
        self._initial_orders_seeded = True

    @staticmethod
    def _sample_poisson(lam: float) -> int:
        """Sample from Poisson(lam) using Knuth's algorithm."""
        if lam <= 0:
            return 0
        threshold = math.exp(-lam)
        p = 1.0
        k = 0
        while p > threshold:
            k += 1
            p *= random.random()
        return k - 1

    def orders_for_next_minute(self) -> int:
        sampled = self._sample_poisson(self.max_orders_per_minute)
        return min(sampled, self.max_orders_per_minute)

    def _get_best_quotes(self):
        """
        Returns best bid/ask from the active book.
        """
        active_orders = self.active_orders or []
        bids = [
            order for order in active_orders
            if int(order.get("order_type")) == int(OrderType.BID)
        ]
        asks = [
            order for order in active_orders
            if int(order.get("order_type")) == int(OrderType.ASK)
        ]
        best_bid = max((order["price"] for order in bids), default=None)
        best_ask = min((order["price"] for order in asks), default=None)
        return best_bid, best_ask

    async def act(self):
        """
        Submit one order of size 1.
        Equally likely actions:
        - market buy
        - market sell
        - limit buy (2 * spread below best bid)
        - limit sell (2 * spread above best ask)
        """
        best_bid, best_ask = self._get_best_quotes()

        # No book at all: re-anchor around last transaction (or default midpoint).
        if best_bid is None and best_ask is None:
            bid_price, ask_price = self._quotes_around(self._reference_price())
            await self.post_new_order(1, bid_price, OrderType.BID)
            await self.post_new_order(1, ask_price, OrderType.ASK)
            return

        # One-sided book: place a restoring limit order on the missing side at default spread distance.
        if best_bid is None:
            bid_price = self._positive_price(best_ask - self.initial_spread)
            await self.post_new_order(1, bid_price, OrderType.BID)
            return
        if best_ask is None:
            ask_price = self._positive_price(best_bid + self.initial_spread)
            await self.post_new_order(1, ask_price, OrderType.ASK)
            return

        spread = best_ask - best_bid
        if spread <= 0:
            return

        action = random.choice(["market_buy", "market_sell", "limit_buy", "limit_sell"])
        if action == "market_buy":
            await self.post_new_order(1, best_ask, OrderType.BID)
        elif action == "market_sell":
            await self.post_new_order(1, best_bid, OrderType.ASK)
        elif action == "limit_buy":
            await self.post_new_order(1, self._positive_price(best_bid - (2 * spread)), OrderType.BID)
        else:
            await self.post_new_order(1, best_ask + (2 * spread), OrderType.ASK)

    async def _sleep_or_stop(self, seconds: float) -> bool:
        """
        Sleep up to `seconds` or return early if stop is requested.
        Returns True when stop is requested, otherwise False.
        """
        if seconds <= 0:
            return self._stop_requested.is_set()
        try:
            await asyncio.wait_for(self._stop_requested.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def run(self):
        """
        Each minute sample a capped Poisson number of orders and distribute attempts
        across that minute.
        """
        while not self._stop_requested.is_set():
            try:
                number_of_orders = self.orders_for_next_minute()
                if number_of_orders <= 0:
                    if await self._sleep_or_stop(60):
                        break
                    continue

                offsets = sorted(random.uniform(0, 60) for _ in range(number_of_orders))
                previous_offset = 0.0
                for offset in offsets:
                    if await self._sleep_or_stop(offset - previous_offset):
                        break
                    if self._stop_requested.is_set():
                        break
                    await self.act()
                    previous_offset = offset

                if self._stop_requested.is_set():
                    break
                if await self._sleep_or_stop(max(0.0, 60.0 - previous_offset)):
                    break

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
