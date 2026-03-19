from json import JSONEncoder
from datetime import datetime, timezone
from functools import wraps
import aio_pika
from enum import Enum
from mongoengine import QuerySet
from uuid import UUID
from bson import ObjectId
from main_platform.custom_logger import setup_custom_logger
from pydantic import BaseModel

logger = setup_custom_logger(__name__)

dict_keys = type({}.keys())
dict_values = type({}.values())

import asyncio
import functools

def if_active(func):
    @functools.wraps(func)
    def sync_wrapper(self, *args, **kwargs):
        if not self.active:
            logger.critical(f"{func.__name__} is skipped because the trading session is not active.")
            return {
                "respond": True,
                "status": "failed",
                "reason": "Market is closed",
            }

        return func(self, *args, **kwargs)

    async def async_wrapper(self, *args, **kwargs):
        if not self.active:
            logger.critical(f"{func.__name__} is skipped because the trading session is not active.")
            return {
                "respond": True,
                "status": "failed",
                "reason": "Market is closed",
            }

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
