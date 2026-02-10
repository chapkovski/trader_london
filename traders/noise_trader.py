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
    ):
        """
        Single noise trader that submits random order types against the human book.
        """
        super().__init__(trader_type=TraderType.NOISE)
        self.max_orders_per_minute = max(0, int(max_orders_per_minute or 0))

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

    def _get_human_best_quotes(self):
        """
        Returns best bid/ask from non-noise orders.
        Noise trader acts only when both sides are present.
        """
        human_orders = [
            order for order in (self.active_orders or [])
            if order.get("trader_id") != self.id
        ]
        human_bids = [
            order for order in human_orders
            if int(order.get("order_type")) == int(OrderType.BID)
        ]
        human_asks = [
            order for order in human_orders
            if int(order.get("order_type")) == int(OrderType.ASK)
        ]
        if not human_bids or not human_asks:
            return None, None, None
        best_bid = max(order["price"] for order in human_bids)
        best_ask = min(order["price"] for order in human_asks)
        if best_ask <= best_bid:
            return None, None, None
        spread = best_ask - best_bid
        return best_bid, best_ask, spread

    async def act(self):
        """
        Submit one order of size 1.
        Equally likely actions:
        - market buy
        - market sell
        - limit buy (2 * spread below best bid)
        - limit sell (2 * spread above best ask)
        """
        best_bid, best_ask, spread = self._get_human_best_quotes()
        if best_bid is None or best_ask is None or spread is None:
            return

        action = random.choice(["market_buy", "market_sell", "limit_buy", "limit_sell"])
        if action == "market_buy":
            await self.post_new_order(1, best_ask, OrderType.BID)
        elif action == "market_sell":
            await self.post_new_order(1, best_bid, OrderType.ASK)
        elif action == "limit_buy":
            await self.post_new_order(1, max(1, best_bid - (2 * spread)), OrderType.BID)
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
