import asyncio

import aio_pika
from fastapi import BackgroundTasks, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketState

from client_connector.trader_manager import TraderManager
from main_platform.custom_logger import setup_custom_logger
from main_platform.trading_platform import SQL_DB, rabbitmq_url
from structures import TraderCreationData

logger = setup_custom_logger(__name__)


def _mask_db_url(url: str) -> str:
    if not url:
        return ""
    if "@" not in url:
        return url
    scheme_split = url.split("://", 1)
    if len(scheme_split) != 2:
        return url
    scheme, rest = scheme_split
    if "@" not in rest:
        return url
    creds, host_part = rest.split("@", 1)
    if ":" in creds:
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host_part}"
    return f"{scheme}://***@{host_part}"


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Runtime in-memory lookup for active sessions/traders.
trader_managers = {}
trader_to_session_lookup = {}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_text()
        await websocket.send_text(f"Message text was: {data}")


@app.get("/traders/defaults")
async def get_trader_defaults():
    schema = TraderCreationData.model_json_schema()
    hidden_fields = {"allow_self_trade"}
    defaults = {
        field: {
            "default": props.get("default"),
            "title": props.get("title"),
            "type": props.get("type"),
            "hint": props.get("description"),
        }
        for field, props in schema.get("properties", {}).items()
        if field not in hidden_fields
    }

    return JSONResponse(content={"status": "success", "data": defaults})


@app.post("/trading/initiate")
async def create_trading_session(params: TraderCreationData, background_tasks: BackgroundTasks):
    trader_manager = TraderManager(params)
    session_uuid = trader_manager.trading_session.id

    all_traders = list(trader_manager.traders.values())
    all_trader_ids = [t.id for t in all_traders]
    human_trader_ids = [t.id for t in trader_manager.human_traders]
    human_trader_set = set(human_trader_ids)

    if SQL_DB.enabled:
        payload = params.model_dump()
        response_data = {
            "trading_session_uuid": session_uuid,
            "traders": all_trader_ids,
            "human_traders": human_trader_ids,
        }
        trader_rows = [
            {
                "trader_uuid": trader.id,
                "trader_type": str(trader.trader_type),
                "is_human": trader.id in human_trader_set,
                "initial_cash": float(getattr(trader, "initial_cash", 0) or 0),
                "initial_shares": float(getattr(trader, "initial_shares", 0) or 0),
            }
            for trader in all_traders
        ]
        try:
            SQL_DB.register_session(session_uuid, payload, response_data=response_data)
            SQL_DB.register_traders(session_uuid, trader_rows)
        except Exception as exc:
            logger.warning(f"SQL registration failed for session {session_uuid}: {exc}")

    background_tasks.add_task(trader_manager.launch)
    trader_managers[session_uuid] = trader_manager

    for trader_uuid in all_trader_ids:
        trader_to_session_lookup[trader_uuid] = session_uuid

    return {
        "status": "success",
        "message": "New trading session created",
        "data": {
            "trading_session_uuid": session_uuid,
            "traders": all_trader_ids,
            "human_traders": human_trader_ids,
        },
    }


def get_manager_by_trader(trader_uuid: str):
    if trader_uuid not in trader_to_session_lookup:
        return None
    trading_session_id = trader_to_session_lookup[trader_uuid]
    return trader_managers.get(trading_session_id)


async def check_rabbitmq():
    target = rabbitmq_url
    try:
        conn = await asyncio.wait_for(
            aio_pika.connect_robust(
                target,
                timeout=2,
                connection_attempts=1,
                reconnect_interval=1,
            ),
            timeout=3,
        )
        await conn.close()
        return True, None, target
    except asyncio.TimeoutError:
        return False, "timeout", target
    except Exception as exc:
        logger.warning(f"RabbitMQ health check failed: {exc}")
        return False, str(exc), target


async def check_database():
    if not SQL_DB.enabled:
        return False, SQL_DB.error or "disabled"
    try:
        loop = asyncio.get_running_loop()
        ok, err = await asyncio.wait_for(
            loop.run_in_executor(None, SQL_DB.healthcheck),
            timeout=3,
        )
        return ok, err
    except asyncio.TimeoutError:
        return False, "timeout"
    except Exception as exc:
        logger.warning(f"Database health check failed: {exc}")
        return False, str(exc)


@app.get("/status")
async def status():
    rabbit_ok, rabbit_err, rabbit_target = await check_rabbitmq()
    db_ok, db_err = await check_database()
    healthy = rabbit_ok and db_ok
    return {
        "status": "ok" if healthy else "degraded",
        "services": {
            "rabbitmq": {"ok": rabbit_ok, "error": rabbit_err, "target": rabbit_target},
            "database": {"ok": db_ok, "error": db_err, "target": _mask_db_url(SQL_DB.db_url)},
        },
    }


@app.get("/debug/persistence-check")
async def persistence_check():
    snapshot = SQL_DB.debug_snapshot()
    snapshot["database_url"] = _mask_db_url(snapshot.get("database_url", ""))
    return {"status": "success", "data": snapshot}


@app.get("/trader/{trader_uuid}")
async def get_trader(trader_uuid: str):
    trader_manager = get_manager_by_trader(trader_uuid)
    if not trader_manager:
        raise HTTPException(status_code=404, detail="Trader not found")
    trader = trader_manager.traders.get(trader_uuid)
    if not trader:
        raise HTTPException(status_code=404, detail="Trader not found")

    data = trader_manager.get_params()
    return {
        "status": "success",
        "message": "Trader found",
        "data": data,
    }


@app.get("/trader_info/{trader_uuid}")
async def get_trader_info(trader_uuid: str):
    trader_manager = get_manager_by_trader(trader_uuid)
    if not trader_manager:
        raise HTTPException(status_code=404, detail="Trader not found")

    trader = trader_manager.get_trader(trader_uuid)
    return {
        "status": "success",
        "message": "Trader found",
        "data": {
            "cash": trader.cash,
            "shares": trader.shares,
            "orders": trader.orders,
            "delta_cash": trader.delta_cash,
            "initial_cash": trader.initial_cash,
            "initial_shares": trader.initial_shares,
        },
    }


@app.get("/trading_session/{trading_session_id}")
async def get_trading_session(trading_session_id: str):
    trader_manager = trader_managers.get(trading_session_id)
    if not trader_manager:
        raise HTTPException(status_code=404, detail="Trading session not found")

    return {
        "status": "found",
        "data": {
            "trading_session_uuid": trader_manager.trading_session.id,
            "traders": list(trader_manager.traders.keys()),
            "human_traders": [t.get_trader_params_as_dict() for t in trader_manager.human_traders],
        },
    }


@app.post("/trading_session/{trading_session_id}/pause")
async def pause_trading_session(trading_session_id: str):
    trader_manager = trader_managers.get(trading_session_id)
    if not trader_manager:
        raise HTTPException(status_code=404, detail="Trading session not found")
    result = await trader_manager.trading_session.pause_session()
    return {
        "status": "success",
        "message": "Pause request processed",
        "data": {
            "trading_session_uuid": trading_session_id,
            **result,
        },
    }


@app.post("/trading_session/{trading_session_id}/resume")
async def resume_trading_session(trading_session_id: str):
    trader_manager = trader_managers.get(trading_session_id)
    if not trader_manager:
        raise HTTPException(status_code=404, detail="Trading session not found")
    result = await trader_manager.trading_session.resume_session()
    return {
        "status": "success",
        "message": "Resume request processed",
        "data": {
            "trading_session_uuid": trading_session_id,
            **result,
        },
    }


@app.websocket("/trader/{trader_uuid}")
async def websocket_trader_endpoint(websocket: WebSocket, trader_uuid: str):
    await websocket.accept()

    trader_manager = get_manager_by_trader(trader_uuid)
    if not trader_manager:
        await websocket.send_json({"status": "error", "message": "Trader not found", "data": {}})
        await websocket.close()
        return

    trader = trader_manager.get_trader(trader_uuid)
    await trader.connect_to_socket(websocket)

    logger.info(f"Trader {trader_uuid} connected to websocket")
    await websocket.send_json(
        {
            "type": "success",
            "message": "Connected to trader",
            "data": {
                "trader_uuid": trader_uuid,
                "order_book": trader.order_book,
            },
        }
    )

    try:
        while True:
            message = await websocket.receive_text()
            if websocket.client_state != WebSocketState.CONNECTED:
                logger.warning(f"Trader {trader_uuid} disconnected")
                break
            await trader.on_message_from_client(message)
    except WebSocketDisconnect:
        logger.critical(f"Trader {trader_uuid} disconnected")
    except asyncio.CancelledError:
        logger.warning("Task cancelled")
        await trader_manager.cleanup()


@app.get("/traders/list")
async def list_traders():
    return {
        "status": "success",
        "message": "List of traders",
        "data": {"traders": sorted(trader_to_session_lookup.keys())},
    }


@app.get("/")
async def root():
    return {
        "status": "trading is active",
        "comment": "This API is used primarily through websocket trader routes",
    }
