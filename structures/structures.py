from enum import Enum, IntEnum, StrEnum
from pydantic import BaseModel

from datetime import datetime
import uuid


class LobsterEventType(IntEnum):
    """For the LOBSTER data, the event type is an integer. This class maps the integer to a string.
    See the documentation at: https://lobsterdata.com/info/DataStructure.php
    """
    NEW_LIMIT_ORDER = 1
    CANCELLATION_PARTIAL = 2
    CANCELLATION_TOTAL = 3
    EXECUTION_VISIBLE = 4
    EXECUTION_HIDDEN = 5
    CROSS_TRADE = 6
    TRADING_HALT = 7


class ActionType(str, Enum):
    POST_NEW_ORDER = 'add_order'
    CANCEL_ORDER = 'cancel_order'
    UPDATE_BOOK_STATUS = 'update_book_status'
    REGISTER = 'register_me'


class OrderType(IntEnum):
    ASK = -1  #  the price a seller is willing to accept for a security
    BID = 1  # the price a buyer is willing to pay for a security


class OrderStatus(str, Enum):
    BUFFERED = 'buffered'
    ACTIVE = 'active'
    EXECUTED = 'executed'
    CANCELLED = 'cancelled'


class TraderType(int, Enum):
    NOISE = 0
    MARKET_MAKER = 1
    INFORMED = 2
    HUMAN = 3


class OrderModel(BaseModel):
    # id: uuid.UUID
    id: uuid.UUID
    amount: float
    price: float
    status: OrderStatus
    order_type: OrderType  # ask or bid
    timestamp: datetime
    # session_id: uuid.UUID
    session_id: str  # FOR TESTING. REMOVE LATER
    trader_id: uuid.UUID


class TransactionModel(BaseModel):
    id: uuid.UUID
    bid_order_id: uuid.UUID
    ask_order_id: uuid.UUID
    timestamp: datetime
    price: float
