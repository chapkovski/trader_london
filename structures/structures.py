from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID, uuid4
from datetime import datetime
from enum import Enum


class CreatingTraderModel(BaseModel):
    cash: float
    stocks: int


class ConnectingTraderModel(BaseModel):
    trader_id: UUID
    session_id: UUID


class TraderModel(BaseModel):
    id: UUID = uuid4
    cash: float
    stocks: int
    blocked_cash: float = 0  # Amount of cash blocked for pending bid orders
    blocked_stocks: int = 0  # Number of stocks blocked for pending ask orders
    joined_at: Optional[datetime] = None
    session_id: Optional[UUID] = None  # Session ID if the trader joins a session


class OrderStatus(str, Enum):
    ACTIVE = "active"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"


class Order(BaseModel):
    id: UUID   # Unique ID for each order
    session_id: UUID  # ID of the trading session this order belongs to
    trader: TraderModel
    order_type: str  # "bid" or "ask"
    quantity: int
    price: float
    order_status: OrderStatus = OrderStatus.ACTIVE  # New field for order status
    created_at: datetime

class NewOrderRequest(BaseModel):
    trader_id: UUID
    session_id: UUID
    order_type: str  # "bid" or "ask"
    quantity: int
    price: float

class Transaction(BaseModel):
    buyer_order: Order
    seller_order: Order
    quantity: int
    price: float
    created_at: datetime


class OrderBookModel(BaseModel):
    active_book: List[Order] = []



class TradingSessionModel(BaseModel):
    id: UUID = uuid4
    active_traders: List[TraderModel] = []
    active_book: List[Order] = []
    full_order_history: List[Order] = []
    transaction_history: List[Transaction] = []
    created_at: datetime







class Error(BaseModel):
    message: str
    created_at: datetime
