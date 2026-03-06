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
        self._applied_dividends = {}

        cash= params.get("initial_cash", 0)
        shares= params.get("initial_stocks", 0)

        # TODO: we may start launching with more than one human trader later.
        # So far for debugging purposes we only need one human trader whose id we return to the client
        n_human_traders = params.get("num_human_traders", 1)
        n_noise_traders = max(0, int(params.get("num_noise_traders", 1) or 0))
        self.noise_traders = [
            NoiseTrader(
                max_orders_per_minute=params.get("max_orders_per_minute", 3),
                initial_midpoint=params.get("initial_midpoint", 100),
                initial_spread=params.get("initial_spread", 10),
                cash=cash,
                shares=shares,
            )
            for _ in range(n_noise_traders)
        ]

        self.human_traders = [HumanTrader(cash=cash, shares=shares) for _ in range(n_human_traders)]

        self.traders = {t.id: t for t in self.noise_traders + self.human_traders}
        self.trading_session = TradingSession(
            duration=params['trading_day_duration'],
            alert_streak_frequency=params.get("alert_streak_frequency", 4),
            alert_window_size=params.get("alert_window_size", 5),
            allow_self_trade=params.get("allow_self_trade", True),
        )



    async def launch(self):
        await self.trading_session.initialize()
        logger.info(f"Trading session UUID: {self.trading_session.id}")

        for trader_id, trader in self.traders.items():
            await trader.initialize()
            await trader.connect_to_session(trading_session_uuid=self.trading_session.id)

        for trader in self.noise_traders:
            await trader.seed_initial_orders()

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

    async def apply_dividend(self, trader_uuid: str, dividend_per_share: float, trading_day: int):
        trader_id = str(trader_uuid or "").strip()
        if not trader_id:
            raise ValueError("Missing trader UUID")
        trader = self.get_trader(trader_id)
        if trader is None:
            raise ValueError("Trader not found")

        day_key = int(trading_day)
        dividend_per_share_value = max(0.0, float(dividend_per_share or 0.0))
        key = (trader_id, day_key)
        existing = self._applied_dividends.get(key)
        if existing:
            return {"changed": False, **existing}

        async with self.trading_session.lock:
            trader_state = self.trading_session.connected_traders.get(trader_id)
            if trader_state is None:
                trader_state = {
                    "trader_type": getattr(trader, "trader_type", "HUMAN"),
                    "cash": float(getattr(trader, "cash", 0.0) or 0.0),
                    "shares": float(getattr(trader, "shares", 0.0) or 0.0),
                    "reserved_cash": 0.0,
                    "reserved_shares": 0.0,
                }
                self.trading_session.connected_traders[trader_id] = trader_state

            shares = float(trader_state.get("shares", getattr(trader, "shares", 0.0)) or 0.0)
            cash_before = float(trader_state.get("cash", getattr(trader, "cash", 0.0)) or 0.0)
            dividend_cash = float(dividend_per_share_value) * float(shares)
            cash_after = float(cash_before) + float(dividend_cash)

            trader_state["shares"] = shares
            trader_state["cash"] = cash_after

            trader.shares = shares
            trader.cash = cash_after

        result = {
            "trader_uuid": trader_id,
            "trading_day": day_key,
            "shares": shares,
            "cash_before_dividend": cash_before,
            "dividend_per_share": dividend_per_share_value,
            "dividend_cash": dividend_cash,
            "cash_after_dividend": cash_after,
        }
        self._applied_dividends[key] = result
        return {"changed": True, **result}

    def get_params(self):
        params = self.params.model_dump()
        trading_session_params = self.trading_session.get_params()
        params.update(trading_session_params)
        return params
