from enum import Enum
from pydantic import BaseModel
import uuid
class OrderStatus(Enum):
    ACTIVE = 'active'
    FULFILLED = 'fulfilled'
    CANCELLED = 'cancelled'

class OrderType(Enum):
    ASK = 'ask'
    BID = 'bid'


from pydantic import BaseModel
import uuid
from datetime import datetime

class OrderModel(BaseModel):
    id: uuid.UUID
    amount: float
    price: float
    status: OrderStatus
    order_type: OrderType  # ask or bid
    timestamp: datetime
    session_id: uuid.UUID
    trader_id: uuid.UUID

