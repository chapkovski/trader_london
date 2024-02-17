import asyncio
from traderabbit.trading_platform import TradingSystem
from traderabbit.trader import Trader

class TraderManager:
    def __init__(self):
        self.traders = {}
        self.lock = asyncio.Lock()

    async def add_trader(self, trader_id: str, trader: Trader):
        async with self.lock:
            if trader_id not in self.traders:
                self.traders[trader_id] = trader
                asyncio.create_task(trader.run())

    async def get_trader(self, trader_id: str):
        async with self.lock:
            return self.traders.get(trader_id)

    async def remove_trader(self, trader_id: str):
        async with self.lock:
            if trader_id in self.traders:
                del self.traders[trader_id]

# Singleton instance of TraderManager
trader_manager = TraderManager()

class SingletonMeta(type):
    _instances = {}
    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            instance = super().__call__(*args, **kwargs)
            cls._instances[cls] = instance
        return cls._instances[cls]

class TradingSystemSingleton(metaclass=SingletonMeta):
    def __init__(self, buffer_delay=5, max_buffer_releases=None):
        self.instance = TradingSystem(buffer_delay=buffer_delay, max_buffer_releases=max_buffer_releases)

def get_trading_system_instance(*args, **kwargs):
    return TradingSystemSingleton(*args, **kwargs).instance