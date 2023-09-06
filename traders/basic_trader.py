import aiohttp
import asyncio
import json
import random
import uuid
import websockets
import argparse


class Trader:
    def __init__(self, base_url, ws_url, session_id, settings, settings_noise, initial_stocks, initial_cash):
        self.base_url = base_url
        self.ws_url = ws_url
        self.session_id = session_id
        self.settings = settings
        self.settings_noise = settings_noise
        self.trader_id = uuid.uuid4()
        self.initial_stocks = initial_stocks
        self.initial_cash = initial_cash
        self.outstanding_orders = {'bid': {}, 'ask': {}}

    async def connect_to_session(self):
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/connect_to_session", json={
                'session_id': self.session_id,
                'trader_id': str(self.trader_id),
                'initial_stocks': self.initial_stocks,
                'initial_cash': self.initial_cash
            }) as response:
                return await response.json()

    async def fetch_market_status(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}/market_status?session_id={self.trading_session_id}") as response:
                return await response.json()

    async def post_order(self, order):
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/post_order", json={
                'session_id': self.trading_session_id,
                'trader_id': self.trader_id,
                'order': order
            }) as response:
                return await response.json()

    def get_noise_rule(self, book):
        # Your existing get_noise_rule logic here
        # Update self.outstanding_orders based on the logic
        pass

    async def market_updates_listener(self):
        async with websockets.connect(self.ws_url) as websocket:
            while True:
                market_update = await websocket.recv()
                self.handle_market_update(json.loads(market_update))

    def handle_market_update(self, update):
        # Your logic to handle real-time market updates
        pass

    async def run(self):
        listener_task = asyncio.create_task(self.market_updates_listener())

        while True:
            market_status = await self.fetch_market_status()
            book = market_status['book']

            self.get_noise_rule(book)

            # Generate noise_order based on book and other settings
            noise_order = {}  # Your logic to generate noise_order

            await self.post_order(noise_order)

            await asyncio.sleep(random.randint(1, 5))  # Random sleep to simulate human behavior

        await listener_task


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run noise traders.')
    parser.add_argument('--n', type=int, default=1, help='Number of noise traders to run')
    parser.add_argument('--session_id', type=str, required=True, help='Session ID to connect traders to')

    args = parser.parse_args()
    num_traders = args.n
    session_id = args.session_id

    settings = {}
    settings_noise = {}
    initial_stocks = 100
    initial_cash = 10000

    traders = [
        Trader("http://localhost:8000", "ws://localhost:8000/ws", session_id, settings, settings_noise, initial_stocks,
               initial_cash)
        for _ in range(num_traders)]

    # Connect each traders to the session
    connect_coroutines = [trader.connect_to_session() for trader in traders]
    asyncio.run(asyncio.gather(*connect_coroutines))

    run_coroutines = [trader.run() for trader in traders]
    asyncio.run(asyncio.gather(*run_coroutines))
