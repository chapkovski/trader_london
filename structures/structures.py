from enum import Enum, IntEnum
try:
    from enum import StrEnum  # Python 3.11+
except ImportError:
    class StrEnum(str, Enum):
        """Minimal StrEnum fallback for Python <3.11."""
        pass
from pydantic import BaseModel, Field, ConfigDict
from mongoengine import Document, IntField, BooleanField, DateTimeField, ListField, DictField, UUIDField, FloatField

from typing import Optional
from uuid import UUID, uuid4
from datetime import datetime, timezone
import uuid


def now():
    """It is actually from utils.py but we need structures there so we do it here to avoid circular deps"""
    return datetime.now(timezone.utc)


class TraderCreationData(BaseModel):
    num_human_traders: int = Field(
        default=1,
        title="Number of Human Traders",
        description="Number of human traders",
        ge=0
    )
    num_days: int = Field(
        default=2,
        title="Number of Days",
        description="Number of trading days in the market",
        ge=1,
    )
    dividends: list[float] = Field(
        default_factory=list,
        title="Dividend Schedule",
        description="Per-day dividend per share values; must be at least num_days long",
    )
    trading_day_duration: int = Field(
        default=2,
        title="Trading Day Duration",
        description="Duration of the trading day in minutes",
        gt=0
    )
    step: int = Field(
        default=1,
        title="Step for New Orders",
        description="Step for new orders", )
    max_orders_per_minute: int = Field(
        default=30,
        title="Noise Trader: Max Orders Per Minute",
        description="Capped Poisson intensity for the single noise trader (0..max orders each minute)",
        ge=0
    )
    noise_trader_start_second: int = Field(
        default=5,
        title="Noise Trader Start Second",
        description="Second within each trading day when noise traders may begin acting",
        ge=0
    )
    num_noise_traders: int = Field(
        default=1,
        title="Number of Noise Traders",
        description="How many algorithmic noise traders to run in the market",
        ge=0
    )
    human_trader_params: list[dict] = Field(
        default_factory=list,
        title="Human Trader Params",
        description="Per-human trader initial balances; one object per human trader",
    )
    initial_midpoint: float = Field(
        default=100,
        title="Initial Midpoint",
        description="Starting midpoint used by the noise trader to seed the first bid/ask",
    )
    initial_spread: float = Field(
        default=10,
        title="Initial Spread",
        description="Starting spread used by the noise trader to seed the first bid/ask",
        gt=0
    )
    initial_cash: float = Field(
        default=2600,
        title="Initial Cash",
        description="Initial cash for each trader",

    )
    initial_stocks: int = Field(
        default=20,
        title="Initial Stocks",
        description="Initial stocks for each trader",

    )
    alert_streak_frequency: int = Field(
        default=3,
        title="Alert Count X",
        description="X moves in the rolling alert window required for an alert",
        ge=1
    )
    alert_window_size: int = Field(
        default=5,
        title="Alert Window Y",
        description="Y recent ticks used for rolling alert checks (X out of Y)",
        ge=1
    )
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
