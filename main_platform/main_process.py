import asyncio
import argparse
from main_platform.custom_logger import setup_custom_logger
from main_platform.trader import Trader
from main_platform.trading_platform import TradingSystem
import signal
from structures.structures import TraderType
import random
import numpy as np
logger = setup_custom_logger(__name__)



# Define the Argument Parser
parser = argparse.ArgumentParser(description='Your Script Description')

# Define Arguments
parser.add_argument('--buffer_delay', type=int, default=0, help='Buffer delay for the Trading System')
parser.add_argument('--max_buffer_releases', type=int, default=None, help='Maximum number of buffer releases')
parser.add_argument('--num_traders', type=int, default=3, help='Number of traders')
parser.add_argument('--seed', type=int, help='Seed for the random number generator', default=None)


async def main(trading_system, traders=()):
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

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(custom_exception_handler)
    args = parser.parse_args()

    # Set the seed here
    if args.seed is not None:
        random.seed(args.seed)
        np.random.seed(args.seed)
    # Use the Arguments
    trading_system = TradingSystem(buffer_delay=args.buffer_delay, max_buffer_releases=args.max_buffer_releases)
    traders = [Trader(trader_type=TraderType.NOISE) for _ in range(args.num_traders)]
    # Initialize TradingSystem with the buffer delay and optionally the max_buffer_releases

    # Add the signal handler for Ctrl+C and SIGTERM
    signal.signal(signal.SIGINT, lambda *args: handle_exit(loop, trading_system, traders))
    signal.signal(signal.SIGTERM, lambda *args: handle_exit(loop, trading_system, traders))

    loop.run_until_complete(main(trading_system, traders))  # Your main async function
