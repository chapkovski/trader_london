import asyncio
import random
from main_platform.custom_logger import setup_custom_logger
from main_platform.trader import Trader
from main_platform.trading_platform import TradingSystem
import signal
from main_platform.main_process import main, handle_exit, async_handle_exit
import argparse
logger = setup_custom_logger(__name__)
# Create an argument parser
parser = argparse.ArgumentParser(description='Run traders in a session.')

# Add command-line arguments
parser.add_argument('--session-id', type=str, default='1234', help='UUID for the trading session.')
parser.add_argument('--num-traders', type=int, default=3, help='Number of traders.')

# Parse the command-line arguments
args = parser.parse_args()

# Extract individual arguments
trading_session_uuid = args.session_id

num_traders = args.num_traders


async def main(trading_session_uuid, traders):
    logger.info(f"Trading session UUID: {trading_session_uuid}")
    for i in traders:
        await i.initialize()
        await i.connect_to_session(trading_session_uuid=trading_session_uuid)

    trader_tasks = []
    for i in traders:
        trader_tasks.append(asyncio.create_task(i.run()))

    await asyncio.gather(*trader_tasks)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()

    traders = [Trader() for _ in range(num_traders)]

    # Add the signal handler for Ctrl+C and SIGTERM
    signal.signal(signal.SIGINT, lambda *args: handle_exit(loop, trading_system=None, traders=traders))
    signal.signal(signal.SIGTERM, lambda *args: handle_exit(loop, trading_system=None, traders=traders))

    loop.run_until_complete(main(trading_session_uuid=trading_session_uuid, traders=traders))  # Your main async function
