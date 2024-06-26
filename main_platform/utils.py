from json import JSONEncoder
from datetime import datetime
from functools import wraps
import aio_pika
from enum import Enum
from mongoengine import QuerySet
from uuid import UUID
from structures.structures import   ActionType, LobsterEventType, OrderType, Order, str_to_order_type
from collections import defaultdict
from typing import List, Dict
import pandas as pd
import numpy as np
from bson import ObjectId
from main_platform.custom_logger import setup_custom_logger

from pydantic import BaseModel
np.set_printoptions(floatmode='fixed', precision=0)

logger = setup_custom_logger(__name__)

dict_keys = type({}.keys())
dict_values = type({}.values())
DATA_PATH = 'data'

from datetime import timezone, datetime

import asyncio
import functools

def if_active(func):
    @functools.wraps(func)
    def sync_wrapper(self, *args, **kwargs):
        if not self.active:
            logger.critical(f"{func.__name__} is skipped because the trading session is not active.")
            return None  # or alternatively, raise an exception

        return func(self, *args, **kwargs)

    async def async_wrapper(self, *args, **kwargs):
        if not self.active:
            logger.critical(f"{func.__name__} is skipped because the trading session is not active.")
            return None  # or alternatively, raise an exception

        return await func(self, *args, **kwargs)

    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper

def now():
    """
    Get the current time in UTC. the datetime.utcnow is a wrong one, because it is not parsed correctly by JS.
    We'll keep it here as a universal function to get the current time, if we later on need to change it to a different
    timezone, we can just do it here.
    """
    return datetime.now(timezone.utc)


class CustomEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, ObjectId):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, BaseModel):
            return obj.model_dump()
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, dict_keys):
            return list(obj)
        if isinstance(obj, dict_values):
            return list(obj)
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, QuerySet):
            # Convert QuerySet to a list of dictionaries
            return [doc.to_mongo().to_dict() for doc in obj]
        return JSONEncoder.default(self, obj)

def ack_message(func):
    @wraps(func)
    async def wrapper(self, message: aio_pika.IncomingMessage):
        await func(self, message)
        await message.ack()

    return wrapper


def expand_dataframe(df, max_depth=10, step=1, reverse=False, default_price=0):
    """
    Expand a DataFrame to a specified max_depth.

    Parameters:
        df (DataFrame): DataFrame containing 'price' and 'amount' columns.
        max_depth (int, optional): Maximum length to which the DataFrame should be expanded. Default is 10.
        step (int, optional): Step to be used for incrementing the price levels. Default is 1.
        reverse (bool, optional): Whether to reverse the order of the DataFrame. Default is False.
        default_price (float, optional): Default starting price if DataFrame is empty. Default is 0.

    Returns:
        DataFrame: Expanded DataFrame.
    """

    # Check if DataFrame is empty
    if df.empty:
        df = pd.DataFrame({'price': [default_price], 'amount': [0]})

    # Sort the DataFrame based on 'price'
    df = df.sort_values('price', ascending=not reverse)

    # Sort the DataFrame based on 'price'
    df.sort_values('price', ascending=not reverse, inplace=True)

    # Calculate the number of additional rows needed
    additional_rows_count = max_depth - len(df)

    if additional_rows_count <= 0:
        return df.iloc[:max_depth]

    # Determine the direction of the step based on 'reverse'
    step_direction = -1 if reverse else 1

    # Generate additional elements for price levels
    last_price = df['price'].iloc[-1 if reverse else 0]
    existing_prices = set(df['price'].values)
    additional_price_elements = []

    i = 1
    while len(additional_price_elements) < additional_rows_count:
        new_price = last_price + i * step * step_direction
        if new_price not in existing_prices:
            additional_price_elements.append(new_price)
            existing_prices.add(new_price)
        i += 1
    # Generate additional elements for amount (all zeros)
    additional_amount_elements = [0] * additional_rows_count

    # Create a DataFrame for the additional elements
    df_additional = pd.DataFrame({'price': additional_price_elements, 'amount': additional_amount_elements})

    # Concatenate the original DataFrame with the additional elements
    df_expanded = pd.concat([df, df_additional]).reset_index(drop=True)

    # Sort the DataFrame based on 'price' if 'reverse' is True
    if reverse:
        df_expanded.sort_values('price', ascending=False, inplace=True)
        df_expanded.reset_index(drop=True, inplace=True)

    return df_expanded


def convert_to_book_format(active_orders, levels_n=10, default_price=2000):
    """ That's the OLD function for Lobster format. I keep it here for now but that all should be deeply rewritten"""
    # Create a DataFrame from the list of active orders

    if active_orders:
        df = pd.DataFrame(active_orders)
    else:
        # Create an empty DataFrame with default columns
        df = pd.DataFrame(columns=[field for field in Order.__annotations__])
    df['price'] = df['price'].round(5)
    df = df.astype({"price": int, "amount": int})

    # Aggregate orders by price, summing the amounts
    df_asks = df[df['order_type'] == 'ask'].groupby('price')['amount'].sum().reset_index().sort_values(
        by='price').head(
        levels_n)
    df_bids = df[df['order_type'] == 'bid'].groupby('price')['amount'].sum().reset_index().sort_values(
        by='price',
        ascending=False).head(
        levels_n)

    df_asks = expand_dataframe(df_asks, max_depth=10, step=1, reverse=False, default_price=default_price)
    df_bids = expand_dataframe(df_bids, max_depth=10, step=1, reverse=True, default_price=default_price - 1)

    ask_prices = df_asks['price'].tolist()

    ask_quantities = df_asks['amount'].tolist()
    bid_prices = df_bids['price'].tolist()

    bid_quantities = df_bids['amount'].tolist()

    # Interleave the lists using np.ravel and np.column_stack
    interleaved_array = np.ravel(np.column_stack((ask_prices, ask_quantities, bid_prices, bid_quantities)))
    interleaved_array = interleaved_array.astype(np.float64)
    return interleaved_array



def convert_to_noise_state(active_orders: List[Dict]) -> Dict:
    noise_state = {
        'outstanding_orders': {
            'bid': defaultdict(int),
            'ask': defaultdict(int)
        }
    }

    for order in active_orders:
        if order.get('status', 'default') == 'active':
            order_type_value = order['order_type']
            order_type = OrderType(order_type_value).name.lower()

            price = order['price']
            amount = order['amount']
            noise_state['outstanding_orders'][order_type][price] += amount

    noise_state['outstanding_orders']['bid'] = dict(noise_state['outstanding_orders']['bid'])
    noise_state['outstanding_orders']['ask'] = dict(noise_state['outstanding_orders']['ask'])

    return noise_state


def convert_to_trader_actions(response_dict):
    actions = []

    for order_type, order_details in response_dict.items():
        _order_type =str_to_order_type[order_type]
        for price, size_list in order_details.items():
            size = size_list[0]
            action = {}
            if size > 0:
                action['action_type'] = ActionType.POST_NEW_ORDER.value
                action['order_type'] = _order_type
                action['price'] = price
                action['amount'] = size
            elif size < 0:
                action['action_type'] = ActionType.CANCEL_ORDER.value
                action['order_type'] = _order_type
                action['price'] = price
            if action:
                actions.append(action)

    return actions


def generate_file_name(session_uuid, file_type, extension='csv'):
    """
    Generate a filename for the given file type and session UUID.
    """
    # Format the current date and time to a string
    date_str = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    # Generate the filename
    file_name = f"{file_type}_{session_uuid}_{date_str}.{extension}"
    # Return the filename prefixed with the "data/" directory
    return f"{DATA_PATH}/{file_name}"
