import asyncio
import argparse
from traderabbit.custom_logger import setup_custom_logger
from traderabbit.trader import HumanTrader, NoiseTrader
import signal
from config.structures import TraderType, params
import random
import numpy as np
from fastapi import FastAPI, WebSocket
import uvicorn
from threading import Thread
from config.shared_resources import trader_manager, get_trading_system_instance
logger = setup_custom_logger(__name__)


# Define the Argument Parser
parser = argparse.ArgumentParser(description='Your Script Description')

# Define Arguments
parser.add_argument('--buffer_delay', type=int, default=params.round.buffer_delay, help='Buffer delay for the Trading System')
parser.add_argument('--max_buffer_releases', type=int, default=params.round.max_buffer_releases, help='Maximum number of buffer releases')
parser.add_argument('--num_traders', type=int, default=params.round.num_traders, help='Number of traders')
parser.add_argument('--seed', type=int, help='Seed for the random number generator', default=params.round.seed)


async def main(trading_system, traders=()):

    print(trader_manager)
    await trading_system.initialize()
    trading_session_uuid = trading_system.id
    logger.info(f"Trading session UUID: {trading_session_uuid}")

    for trader in traders:
        await trader.initialize()
        await trader.connect_to_session(trading_session_uuid=trading_session_uuid)

    await trading_system.send_broadcast({"content": "Market is open"})

    trading_system_task = asyncio.create_task(trading_system.run())
    trader_tasks = [asyncio.create_task(i.run()) for i in traders]

    await trading_system_task

    # Once the trading system has stopped, cancel all trader tasks
    for task in trader_tasks:
        task.cancel()

    # Optionally, wait for all trader tasks to be cancelled
    try:
        await asyncio.gather(*trader_tasks, return_exceptions=True)
    except Exception:
        # Handle the propagated exception here (e.g., log it, perform cleanup)
        # Optionally re-raise the exception
        raise

async def async_handle_exit(loop, trading_system=None, traders=()):
    if trading_system:
        await trading_system.clean_up()
    for i in traders:
        await i.clean_up()

    # Cancel all running tasks
    for task in asyncio.all_tasks(loop=loop):
        task.cancel()
        print('task cancelled!')

    # Allow time for the tasks to cancel
    await asyncio.sleep(1)

    loop.stop()

def handle_exit(loop, trading_system=None, traders=()):
    loop.create_task(async_handle_exit(loop, trading_system, traders))

def custom_exception_handler(loop, context):
    # First, handle the exception however you want
    logger.critical(f"Caught an unhandled exception: {context['exception']}")
    # Then, stop the event loop
    loop.stop()

async def main_with_timeout(trading_system, traders, timeout_seconds=5):
    try:
        # Wrap the main coroutine with wait_for to enforce a timeout
        await asyncio.wait_for(main(trading_system, traders), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        print("The main function has timed out after", timeout_seconds, "seconds")
    

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    # asyncio.set_event_loop(loop)
    loop.set_exception_handler(custom_exception_handler)
    args = parser.parse_args()

    # Set the seed here
    if args.seed is not None:
        random.seed(args.seed)
        np.random.seed(args.seed)

    # Use the Arguments
    # trading_system = TradingSystem(buffer_delay=args.buffer_delay, max_buffer_releases=args.max_buffer_releases)
    trading_system = get_trading_system_instance(buffer_delay=args.buffer_delay, max_buffer_releases=args.max_buffer_releases)

    # Initialize Noise Traders
    noise_traders = [NoiseTrader(trader_type=TraderType.NOISE) for _ in range(params.round.num_noise_traders)]

    # Combine the human trader with the noise traders
    traders = noise_traders

    # Add the signal handler for Ctrl+C and SIGTERM
    signal.signal(signal.SIGINT, lambda *args: handle_exit(loop, trading_system, traders))
    signal.signal(signal.SIGTERM, lambda *args: handle_exit(loop, trading_system, traders))

    def start_uvicorn():
        config = uvicorn.Config("app:app", host="127.0.0.1", port=8000, loop="asyncio")
        server = uvicorn.Server(config)
        server.run()


    uvicorn_thread = Thread(target=start_uvicorn)
    uvicorn_thread.start()

    try:
        loop.run_until_complete(main(trading_system, traders))
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()