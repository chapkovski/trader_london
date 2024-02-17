from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, BackgroundTasks
from pydantic import BaseModel, Field
from config.shared_resources import trader_manager, get_trading_system_instance
import uuid
import asyncio
from config.structures import TraderType
from traderabbit.trader import HumanTrader

trading_system_instance = get_trading_system_instance()

app = FastAPI()

class OrderMessageModel(BaseModel):
    amount: float
    price: float
    order_type: str
    trader_id: str

class CancelOrderModel(BaseModel):
    order_id: str
    trader_id: str

class TraderCreationData(BaseModel):
    max_short_shares: int = Field(default=100, description="Maximum number of shares for shorting")
    max_short_cash: float = Field(default=10000.0, description="Maximum amount of cash for shorting")
    initial_cash: float = Field(default=1000.0, description="Initial amount of cash")
    initial_shares: int = Field(default=0, description="Initial amount of shares")
    trading_day_duration: int = Field(default=5, description="Duration of the trading day in minutes")
    max_active_orders: int = Field(default=5, description="Maximum amount of active orders")
    noise_trader_update_freq: int = Field(default=10, description="Frequency of noise traders' updates in seconds")
    step: int = Field(default=100, description="Step for new orders")

    class Config:
        schema_extra = {
            "example": {
                "max_short_shares": 100,
                "max_short_cash": 10000.0,
                "initial_cash": 1000.0,
                "initial_shares": 0,
                "trading_day_duration": 5,  # Representing 8 hours in minutes
                "max_active_orders": 5,
                "noise_trader_update_freq": 10,  # in seconds,
                "step": 100
            }
        }

# class TraderManager:
#     def __init__(self):
#         self.traders = {}  # Maps trader_id to trader's message queue

#     def add_trader(self, trader_id, message_queue):
#         self.traders[trader_id] = message_queue

#     def get_trader(self, trader_id):
#         return self.traders.get(trader_id)
    

# # Instantiate TraderManager
# trader_manager = TraderManager()

# Define a global variable for the message handler
message_handler = None

def get_message_handler():
    def default_handler(message: str):
        if message_handler:
            message_handler(message)
        else:
            print("Message handler not set.")
    return default_handler

# Add this setter function
def set_message_handler(handler):
    global message_handler
    message_handler = handler

@app.post("/send-order/")
async def send_order(order: OrderMessageModel):
    # Retrieve the HumanTrader instance
    human_trader = trader_manager.get_trader(order.trader_id)
    if human_trader:
        # Use the trader's message_queue to enqueue the order
        await human_trader.message_queue.put(order.dict())
        return {"message": "Order sent to trader", "content": order.dict()}
    else:
        raise HTTPException(status_code=404, detail="Trader not found")

@app.post("/traders/register")
async def register_trader(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    trader_id = body.get("trader_id")
    if not trader_id:
        raise HTTPException(status_code=400, detail="Missing trader_id")

    trader = await trader_manager.get_trader(trader_id)
    if trader:
        return {"message": "Trader already registered", "trader_id": trader_id}

    new_trader = HumanTrader(trader_type=TraderType.HUMAN, trader_id=trader_id)
    await trader_manager.add_trader(trader_id, new_trader)

    return {"trader_id": trader_id, "message": "Trader registered successfully"}

@app.get("/traders/defaults")
async def get_trader_defaults():
    # Placeholder for getting trader defaults
    return {"status": "success", "data": {}}

@app.post("/traders/create")
async def create_trader(trader_data: TraderCreationData):
    # Placeholder for creating a new trader
    return {"status": "success", "message": "New trader created", "data": {}}

@app.websocket("/trader/{trader_uuid}")
async def websocket_trader_endpoint(websocket: WebSocket, trader_uuid: str):
    # Placeholder for WebSocket connection with a trader
    await websocket.accept()
    try:
        while True:
            # Placeholder for handling WebSocket messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        # Placeholder for handling WebSocket disconnection
        pass

@app.post("/cancel-order/")
async def cancel_order(request: CancelOrderModel):
    # Fetch the trader's message queue using the trader_id
    trader_queue = trader_manager.get_trader_queue(request.trader_id)
    if trader_queue is not None:
        # Create a cancel order request
        cancel_request = {"action": "cancel_order", "order_id": request.order_id}
        # Put the cancel order request into the trader's message queue
        await trader_queue.put(cancel_request)
        return {"message": "Cancel order request sent", "order_id": request.order_id}
    else:
        # If the trader_id does not match any existing traders, raise an HTTPException
        raise HTTPException(status_code=404, detail="Trader not found")

@app.get("/traders/list")
async def list_traders():
    # Placeholder for listing traders
    return {"status": "success", "message": "List of traders", "data": {}}

@app.get("/orders/")
async def get_orders():
    if trading_system_instance is None:
        raise HTTPException(status_code=500, detail="Trading system not initialized")
    try:
        orders = trading_system_instance.list_active_orders
        return {"orders": orders}
    except Exception as e:
        print(f"Failed to fetch orders: {e}")  # Ensure this log is showing up in your console
        raise HTTPException(status_code=500, detail=e)

@app.get("/")
async def root():
    # Placeholder for the root endpoint
    return {"status": "trading is active", "comment": "this is only for accessing trading platform mostly via websockets"}
