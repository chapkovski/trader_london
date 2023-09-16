from enum import Enum
from pydantic import BaseModel
import uuid
class OrderStatus(Enum):
    ACTIVE = 'active'
    FULFILLED = 'fulfilled'
    CANCELLED = 'cancelled'


class ActionType(Enum):
    POST_NEW_ORDER = 'add_order'
    CANCEL_ORDER = 'cancel_order'
    UPDATE_BOOK_STATUS = 'update_book_status'
    REGISTER = 'register_me'

class OrderType(Enum):
    ASK = 'ask'
    BID = 'bid'


from pydantic import BaseModel
import uuid
from datetime import datetime

class OrderModel(BaseModel):
    # id: uuid.UUID
    id: uuid.UUID
    amount: float
    price: float
    status: OrderStatus
    order_type: OrderType  # ask or bid
    timestamp: datetime
    # session_id: uuid.UUID
    session_id: str # FOR TESTING. REMOVE LATER
    trader_id: uuid.UUID

