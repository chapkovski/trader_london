from pydantic import BaseModel
from typing import List
from uuid import UUID, uuid4
class Trader(BaseModel):
    id: UUID = uuid4()
    cash: float
    stocks: int

class Order(BaseModel):
    trader: Trader
    order_type: str  # "bid" or "ask"
    quantity: int
    price: float
    active: bool = True  # Marker for active orders

class Transaction(BaseModel):
    buyer_order: Order
    seller_order: Order
    quantity: int
    price: float

class TradingSessionModel(BaseModel):
    id: UUID = uuid4()
    active_traders: List[Trader] = []
    transaction_history: List[Transaction] = []
    active_book: List[Order] = []
