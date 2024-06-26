from enum import Enum, IntEnum, StrEnum
from pydantic import BaseModel, Field, ConfigDict
from mongoengine import Document, IntField, BooleanField, DateTimeField, ListField, DictField, UUIDField, FloatField

from typing import Optional
from uuid import UUID, uuid4
from datetime import datetime, timezone
import uuid


def now():
    """It is actually from utils.py but we need structures there so we do it here to avoid circular deps"""
    return datetime.now(timezone.utc)


GOALS = [-10, 0, 10]  # for now let's try a naive hardcoded approach to the goals


class TradeDirection(str, Enum):
    BUY = 'buy'
    SELL = 'sell'


class TraderCreationData(BaseModel):
    num_human_traders: int = Field(
        default=1,
        title="Number of Human Traders",
        description="Number of human traders",
        ge=0
    )
    num_noise_traders: int = Field(
        default=1,
        title="Number of Noise Traders",
        description="Number of noise traders",
        ge=0
    )
    num_informed_traders: int = Field(
        default=1,
        title="Number of Informed Traders",
        description="Number of informed traders",
        ge=0
    )
    trading_day_duration: int = Field(
        default=1,
        title="Trading Day Duration",
        description="Duration of the trading day in minutes",
        gt=0
    )
    step: int = Field(
        default=1,
        title="Step for New Orders",
        description="Step for new orders", )
    activity_frequency: float = Field(
        default=1.0,
        title="Noise Trader: Activity Frequency",
        description="Frequency of noise traders' updates in seconds",
        gt=0
    )
    order_amount: int = Field(
        default=1,
        title="Noise Trader: Order Amount",
        description="Order amount for noise traders",
    )
    trade_intensity_informed: float = Field(
        default=0.1,
        title="Informed Trader: Trade Intensity",
        description="Trade intensity for informed traders, e.g. she will trade 30 percent of the total orders",
    )
    trade_direction_informed: TradeDirection = Field(
        default=TradeDirection.SELL,
        title="Informed Trader: Trade Direction",
        description="Trade direction for informed traders, to sell or buy",
    )
    noise_warm_ups: int = Field(
        default=10,
        title="Noise Warm Ups",
        description="Number of warm up periods for noise traders",
        gt=0
    )
    initial_cash: float = Field(
        default=100000,
        title="Initial Cash",
        description="Initial cash for each trader",

    )
    initial_stocks: int = Field(
        default=100,
        title="Initial Stocks",
        description="Initial stocks for each trader",

    )
    depth_book_shown: int = Field(
        default=5,
        title="Depth Book Shown",
        description="Depth of the book shown to the human traders",

    )


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
    ASK = -1  # the price a seller is willing to accept for a security
    BID = 1  # the price a buyer is willing to pay for a security

# let's write an inverse correspondence between the order type and the string
str_to_order_type = {
    'ask': OrderType.ASK,
    'bid': OrderType.BID
}


class OrderStatus(str, Enum):
    BUFFERED = 'buffered'
    ACTIVE = 'active'
    EXECUTED = 'executed'
    CANCELLED = 'cancelled'


class TraderType(str, Enum):
    NOISE = 'NOISE'
    MARKET_MAKER = 'MARKET_MAKER'
    INFORMED = 'INFORMED'
    HUMAN = 'HUMAN'
    # Philipp: expand this list to include new trader types if needed


class Order(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    status: OrderStatus
    amount: float = 1  # Assuming amount is a float, adjust the type as needed
    price: float  # Same assumption as for amount
    order_type: OrderType
    timestamp: datetime = Field(default_factory=now)
    session_id: str
    trader_id: str

    class ConfigDict:
        use_enum_values = True  # This ensures that enum values are used in serialization


class TransactionModel(Document):
    id = UUIDField(primary_key=True, default=uuid.uuid4, binary=False)
    trading_session_id = UUIDField(required=True, binary=False)  # Store as string
    bid_order_id = UUIDField(required=True, binary=False)  # Store as string
    ask_order_id = UUIDField(required=True, binary=False)  # Store as string
    timestamp = DateTimeField(default=datetime.now)
    price = FloatField(required=True)


class Message(Document):
    trading_session_id = UUIDField(required=True, binary=False)  # Assuming you want the UUID as a string
    content = DictField(required=True)  # Store the entire message as a dictionary
    timestamp = DateTimeField(default=datetime.now)  # Automatically set the timestamp when created
