"""
This file is for the trader manager: it is needed for connectingn, launching and managing human traders connections.
Upon the request  of a client, it lauchnes new trading sessions and/or helps finding the exsiting traders and return their ids back to clients
so they can communicate with them.
"""

from structures import TraderCreationData
from typing import List
import random
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
        self._started = False
        self._finalized = False
        self.is_simulated = bool(params.get("is_simulated", False))
        self._simulation_rng = random.Random(0)

        cash = float(params.get("initial_cash", 0) or 0)
        shares = max(0, int(params.get("initial_stocks", 0) or 0))
        self.initial_midpoint = float(params.get("initial_midpoint", 100) or 100)
        self.initial_spread = max(1.0, float(params.get("initial_spread", 10) or 10))
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
                start_second=params.get("noise_trader_start_second", 5),
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
            day_duration_minutes=self.trading_day_duration,
            num_days=self.num_days,
            noise_trader_start_second=params.get("noise_trader_start_second", 5),
        )
        self.trading_session.on_session_closure = self.apply_remaining_dividends

    async def startup(self):
        if self._started:
            return
        if self.is_simulated:
            await self.trading_session.initialize_simulated()
            logger.info(f"Trading session UUID: {self.trading_session.id} (simulated)")
            for trader in self.traders.values():
                trader.trading_session = self.trading_session
                trader.trading_session_uuid = self.trading_session.id
                await self.trading_session.handle_register_me(
                    {
                        "trader_id": trader.id,
                        "trader_type": trader.trader_type,
                        "cash": trader.cash,
                        "shares": trader.shares,
                    }
                )
            self._started = True
            return
        await self.trading_session.initialize()
        logger.info(f"Trading session UUID: {self.trading_session.id}")

        for trader_id, trader in self.traders.items():
            trader.trading_session = self.trading_session
            await trader.initialize()
            await trader.connect_to_session(trading_session_uuid=self.trading_session.id)

        await self.trading_session.send_broadcast({"content": "Market is open"})
        self._started = True

    async def launch(self):
        await self.startup()
        if self.is_simulated:
            return

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

    async def finalize_session(self):
        if self._finalized:
            return {"changed": False, "status": "closed"}

        closure_result = None
        closure_callback = getattr(self.trading_session, "on_session_closure", None)
        if callable(closure_callback):
            closure_result = closure_callback()
            if asyncio.iscoroutine(closure_result):
                closure_result = await closure_result

        self.trading_session.active = False
        warning = None
        try:
            await self.trading_session.send_broadcast({"type": "closure"})
        except Exception as exc:
            warning = str(exc)
            logger.warning(f"finalize_session broadcast failed for session {self.trading_session.id}: {exc}")

        await self.cleanup()
        self._finalized = True
        result = {"changed": True, "status": "closed", "closure_result": closure_result}
        if warning:
            result["broadcast_warning"] = warning
        return result

    def get_trader(self, trader_uuid):
        return self.traders.get(trader_uuid)

    def exists(self, trader_uuid):
        return trader_uuid in self.traders

    def _next_unapplied_day(self):
        for day in range(1, self.num_days + 1):
            if day not in self._applied_dividend_days:
                return day
        return self.num_days + 1

    async def generate_simulated_day_activity(self):
        if not self.is_simulated:
            return {"changed": False, "reason": "not_simulated"}
        if not self._started:
            await self.startup()
        if not self.trading_session.active:
            return {"changed": False, "reason": "inactive"}
        if self.trading_session.paused:
            return {"changed": False, "reason": "already_paused"}
        if len(self.human_traders) < 2:
            return {"changed": False, "reason": "need_two_humans"}

        day_index = min(self._next_unapplied_day(), self.num_days)
        buyer, seller = self.human_traders[0], self.human_traders[1]
        if day_index % 2 == 0:
            buyer, seller = seller, buyer

        anchor_price = self.trading_session.transaction_price
        if anchor_price is None:
            anchor_price = self.initial_midpoint
        drift = self._simulation_rng.choice([-1, 0, 1]) + (day_index - 1)
        trade_price = max(1, int(round(float(anchor_price) + drift)))
        passive_bid_price = max(1, trade_price - max(1, int(round(self.initial_spread / 2.0))))
        passive_ask_price = trade_price + max(1, int(round(self.initial_spread / 2.0)))

        actions = [
            dict(trader_id=buyer.id, amount=1, price=trade_price, order_type=1),
            dict(trader_id=seller.id, amount=1, price=trade_price, order_type=-1),
            dict(trader_id=buyer.id, amount=1, price=passive_bid_price, order_type=1),
            dict(trader_id=seller.id, amount=1, price=passive_ask_price, order_type=-1),
        ]
        results = []
        for action in actions:
            results.append(await self.trading_session.handle_add_order(dict(action)))

        return {
            "changed": True,
            "day_index": day_index,
            "trade_price": trade_price,
            "last_transaction_price": self.trading_session.transaction_price,
            "results": results,
        }

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
        simulation_result = None
        if self.is_simulated:
            simulation_result = await self.generate_simulated_day_activity()
        result = await self.trading_session.pause_session()
        dividend_result = None
        if result.get("changed"):
            day_to_apply = self._next_unapplied_day()
            if day_to_apply <= (self.num_days - 1):
                dividend_result = await self.apply_dividend_for_day(day_to_apply)
        return {**result, "dividend_result": dividend_result, "simulation_result": simulation_result}

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
                terminal_settlement[trader_id] = {
                    "shares_before_terminal_settlement": shares_before,
                    "reserved_shares_before_terminal_settlement": reserved_shares_before,
                    "shares_after_terminal_settlement": shares_before,
                    "reserved_shares_after_terminal_settlement": reserved_shares_before,
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
