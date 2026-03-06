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
        params = params.model_dump()
        logger.critical(f"TraderManager params: {params}")
        self.tasks = []
        self._applied_dividend_days = set()

        cash = float(params.get("initial_cash", 0) or 0)
        shares = max(0, int(params.get("initial_stocks", 0) or 0))
        self.num_days = max(1, int(params.get("num_days", 1) or 1))
        self.trading_day_duration = max(1, int(params.get("trading_day_duration", 1) or 1))
        self.dividends = [max(0.0, float(x or 0.0)) for x in (params.get("dividends") or [])]
        if len(self.dividends) < self.num_days:
            raise ValueError(
                f"Dividend schedule has {len(self.dividends)} values but num_days={self.num_days}."
            )
        self.dividends = self.dividends[: self.num_days]

        # TODO: we may start launching with more than one human trader later.
        # So far for debugging purposes we only need one human trader whose id we return to the client
        n_human_traders = params.get("num_human_traders", 1)
        n_noise_traders = max(0, int(params.get("num_noise_traders", 1) or 0))
        human_trader_params = params.get("human_trader_params") or []
        if human_trader_params and len(human_trader_params) != int(n_human_traders):
            raise ValueError(
                f"human_trader_params length ({len(human_trader_params)}) "
                f"must equal num_human_traders ({n_human_traders})."
            )
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

        if human_trader_params:
            self.human_traders = [
                HumanTrader(
                    cash=max(0.0, float(item.get("initial_cash", cash) or 0.0)),
                    shares=max(0, int(item.get("initial_stocks", shares) or 0)),
                )
                for item in human_trader_params
            ]
        else:
            self.human_traders = [HumanTrader(cash=cash, shares=shares) for _ in range(n_human_traders)]

        self.traders = {t.id: t for t in self.noise_traders + self.human_traders}
        self.trading_session = TradingSession(
            duration=self.trading_day_duration * self.num_days,
            alert_streak_frequency=params.get("alert_streak_frequency", 4),
            alert_window_size=params.get("alert_window_size", 5),
            allow_self_trade=params.get("allow_self_trade", True),
        )
        self.trading_session.on_session_closure = self.apply_remaining_dividends



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

    def _next_unapplied_day(self):
        for day in range(1, self.num_days + 1):
            if day not in self._applied_dividend_days:
                return day
        return self.num_days + 1

    async def apply_dividend_for_day(self, trading_day: int):
        day_key = int(trading_day or 0)
        if day_key < 1 or day_key > self.num_days:
            raise ValueError(f"trading_day must be in [1, {self.num_days}]")
        if day_key in self._applied_dividend_days:
            return {
                "changed": False,
                "trading_day": day_key,
                "dividend_per_share": float(self.dividends[day_key - 1]),
                "already_applied": True,
            }

        dividend_per_share = float(self.dividends[day_key - 1])
        per_trader = {}
        async with self.trading_session.lock:
            for trader_id, trader in self.traders.items():
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
                reserved_shares = float(trader_state.get("reserved_shares", 0.0) or 0.0)
                total_shares = shares + reserved_shares
                cash_before = float(trader_state.get("cash", getattr(trader, "cash", 0.0)) or 0.0)
                dividend_cash = float(dividend_per_share) * float(total_shares)
                cash_after = float(cash_before) + float(dividend_cash)
                trader_state["shares"] = shares
                trader_state["cash"] = cash_after
                trader.shares = shares
                trader.cash = cash_after
                per_trader[trader_id] = {
                    "trader_uuid": trader_id,
                    "shares": shares,
                    "reserved_shares": reserved_shares,
                    "total_shares_for_dividend": total_shares,
                    "cash_before_dividend": cash_before,
                    "dividend_cash": dividend_cash,
                    "cash_after_dividend": cash_after,
                }
            self._applied_dividend_days.add(day_key)

        return {
            "changed": True,
            "trading_day": day_key,
            "dividend_per_share": dividend_per_share,
            "traders": per_trader,
        }

    async def pause_session(self):
        result = await self.trading_session.pause_session()
        dividend_result = None
        if result.get("changed"):
            day_to_apply = self._next_unapplied_day()
            if day_to_apply <= (self.num_days - 1):
                dividend_result = await self.apply_dividend_for_day(day_to_apply)
        return {**result, "dividend_result": dividend_result}

    async def apply_remaining_dividends(self):
        summaries = []
        for day in range(1, self.num_days + 1):
            if day not in self._applied_dividend_days:
                summaries.append(await self.apply_dividend_for_day(day))
        terminal_settlement = {}
        async with self.trading_session.lock:
            for trader_id, trader in self.traders.items():
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
                shares_before = float(trader_state.get("shares", 0.0) or 0.0)
                reserved_shares_before = float(trader_state.get("reserved_shares", 0.0) or 0.0)
                trader_state["shares"] = 0.0
                trader_state["reserved_shares"] = 0.0
                trader.shares = 0.0
                terminal_settlement[trader_id] = {
                    "shares_before_terminal_settlement": shares_before,
                    "reserved_shares_before_terminal_settlement": reserved_shares_before,
                    "shares_after_terminal_settlement": 0.0,
                    "reserved_shares_after_terminal_settlement": 0.0,
                }
        return {
            "num_days": self.num_days,
            "applied_days": sorted(self._applied_dividend_days),
            "results": summaries,
            "terminal_settlement": terminal_settlement,
        }

    def get_params(self):
        params = self.params.model_dump()
        trading_session_params = self.trading_session.get_params()
        params.update(trading_session_params)
        return params
