from pydantic import BaseModel
from typing import List
from uuid import UUID, uuid4


class TraderModel(BaseModel):
    id: UUID = uuid4
    cash: float
    stocks: int
    blocked_cash: float = 0  # Amount of cash blocked for pending bid orders
    blocked_stocks: int = 0  # Number of stocks blocked for pending ask orders


class Order(BaseModel):
    trader: TraderModel
    order_type: str  # "bid" or "ask"
    quantity: int
    price: float
    active: bool = True  # Marker for active orders


class OrderBookModel(BaseModel):
    active_book: List[Order] = []
    full_order_history: List[Order] = []


class Transaction(BaseModel):
    buyer_order: Order
    seller_order: Order
    quantity: int
    price: float


class TradingSessionModel(BaseModel):
    id: UUID = uuid4()
    active_traders: List[TraderModel] = []
    active_book: List[Order] = []
    full_order_history: List[Order] = []
    transaction_history: List[Transaction] = []


class Error(BaseModel):
    message: str
