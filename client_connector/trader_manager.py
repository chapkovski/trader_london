"""
This file is for the trader manager: it is needed for connectingn, launching and managing human traders connections.
Upon the request  of a client, it lauchnes new trading sessions and/or helps finding the exsiting traders and return their ids back to clients
so they can communicate with them.
"""

from structures import TraderCreationData
from typing import List
from traders import HumanTrader, NoiseTrader

from main_platform import TradingSession

import asyncio

from main_platform.custom_logger import setup_custom_logger

logger = setup_custom_logger(__name__)


class TraderManager:
    params: TraderCreationData
    trading_system: TradingSession = None
    traders = {}
    human_traders = List[HumanTrader]
    noise_traders = List[NoiseTrader]

    def __init__(self, params: TraderCreationData):

        self.params = params
        params=params.model_dump()
        logger.critical(f"TraderManager params: {params}")
        self.tasks = []
        n_noise_traders = params.get("num_noise_traders", 1)

        cash= params.get("initial_cash", 0)
        shares= params.get("initial_stocks", 0)

        # TODO: we may start launching with more than one human trader later.
        # So far for debugging purposes we only need one human trader whose id we return to the client
        n_human_traders = params.get("num_human_traders", 1)
        self.noise_warm_ups = params.get("noise_warm_ups", 10)

        base_price = params.get("initial_price", 1000)
        noise_settings = {
            "initial_price": base_price,
            "price_step": params.get("step", 1),
            "cancel_probability": 0.25,
            "max_price_deviation": 5,
        }
        self.noise_traders = [NoiseTrader(activity_frequency=params.get('activity_frequency'),
                                          order_amount=params.get('order_amount'), 
                                          settings=noise_settings) for _ in range(n_noise_traders)]

        self.human_traders = [HumanTrader(cash=cash, shares=shares) for _ in range(n_human_traders)]

        self.traders = {t.id: t for t in self.noise_traders + self.human_traders}
        self.trading_session = TradingSession(
            duration=params['trading_day_duration'],
            alert_streak_frequency=params.get("alert_streak_frequency", 4),
        )



    async def launch(self):
        await self.trading_session.initialize()
        logger.info(f"Trading session UUID: {self.trading_session.id}")

        for trader_id, trader in self.traders.items():
            await trader.initialize()
            await trader.connect_to_session(trading_session_uuid=self.trading_session.id)

        for trader in self.noise_traders:
            # Seed the book with a few orders so early spreads are non-empty.
            await trader.warm_up(number_of_warmup_orders=self.noise_warm_ups)

        await self.trading_session.send_broadcast({"content": "Market is open"})

        # Run the session loop and all traders concurrently.
        trading_session_task = asyncio.create_task(self.trading_session.run())
        trader_tasks = [asyncio.create_task(i.run()) for i in self.traders.values()]

        self.tasks.append(trading_session_task)
        self.tasks.extend(trader_tasks)

        await trading_session_task

    async def cleanup(self):
        await self.trading_session.clean_up()
        for trader in self.traders.values():
            await trader.clean_up()
        for task in self.tasks:
            task.cancel()  # Request cancellation of the task
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()  # Clear the list of tasks after cancellation

    def get_trader(self, trader_uuid):
        return self.traders.get(trader_uuid)

    def exists(self, trader_uuid):
        return trader_uuid in self.traders

    def get_params(self):
        params = self.params.model_dump()
        trading_session_params = self.trading_session.get_params()
        params.update(trading_session_params)
        return params
