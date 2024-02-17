from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect
from human_trader import HumanTrader
from fastapi.middleware.cors import CORSMiddleware
import uuid
from structs import TraderCreationData

class TraderManager:
    def __init__(self):
        self.traders = {}

    def create_new_trader(self, trader_data: TraderCreationData):
        trader = HumanTrader(trader_data)
        self.traders[trader.uuid] = trader
        return trader


    def get_trader(self, trader_uuid):
        return self.traders.get(trader_uuid)

    def exists(self, trader_uuid):
        return trader_uuid in self.traders

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)


trader_manager = TraderManager()


@app.get("/traders/defaults")
async def get_trader_defaults():
    # Get the schema of the model
    schema = TraderCreationData.schema()

    # Extract default values from the schema
    defaults = {field: props.get("default") for field, props in schema.get("properties", {}).items() if
                "default" in props}

    return {
        "status": "success",
        "data": defaults
    }


@app.post("/traders/create")
async def create_trader(trader_data: TraderCreationData):
    new_trader = trader_manager.create_new_trader(trader_data)
    return {
        "status": "success",
        "message": "New trader created",
        "data": {"trader_uuid": new_trader.uuid}
    }



@app.websocket("/trader/{trader_uuid}")
async def websocket_trader_endpoint(websocket: WebSocket, trader_uuid: str):
    await websocket.accept()

    if not trader_manager.exists(trader_uuid):
        await websocket.send_json({
            "status": "error",
            "message": "Trader not found",
            "data": {}
        })
        await websocket.close()
        return

    trader = trader_manager.get_trader(trader_uuid)
    trader.start_updates(websocket)

    # Send current status immediately upon new connection
    await websocket.send_json({
        "type": "success",
        "message": "Connected to trader",
        "data": {
            "trader_uuid": trader_uuid,
            "order_book": trader.order_book,
            "history": trader.transaction_history
        }
    })

    try:
        while True:
            message = await websocket.receive_text()
            await trader.handle_incoming_message(message)
    except WebSocketDisconnect:
        trader.stop_updates()
        # Additional disconnection handling (logging, cleanup, etc.)

@app.get("/traders/list")
async def list_traders():
    return {
        "status": "success",
        "message": "List of traders",
        "data": {"traders": list(trader_manager.traders.keys())}
    }


@app.get("/")
async def root():
    return {"status": "trading is active", "comment":"this is only for accessing trading platform mostly via websockets"}