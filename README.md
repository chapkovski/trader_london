# Short intro

The FastAPI app that launches the system is in the `client_connector` subfolder.

To launch it you need to do:

```bash
cd trading_platform_app
uvicorn client_connector.main:app --reload --port 8001
```

the endpoint to launch the trading system is `http://localhost:8001/trading/initiate`
and it will return you the human trader id.
Using this trader id you can connect using websockets to
`ws://localhost:8001/trader/{trader_id}`

The fastapi (in client_connector/main.py) launches the TraderManager (from client_connector/trader_manager.py) which is responsible for launching the trading_session and creating and managing the traders.

The code for trading_session in `main_platform/trading_platform.py`
The code for single traders is in folder `traders`
There we have:

- base_trader.py - Where the BaseTrader class is located
- human_trader.py - Where the HumanTrader subclass of BaseTrader is located
- noise_trader.py - Where the NoiseTrader subclass of BaseTrader is located. The noise trader is a prototype and uses a simple random strategy.

the folder `structures` contain the dataclasses used in the project. I want to keep all the structure definitions in one place.

# How It Works

This system is built around a single trading session with multiple traders connected through a messaging layer.

## Roles

- Traders: the session runs human traders plus automated noise traders. Each trader connects to the session and receives market updates; they submit new/cancel order requests back to the platform.
- Trading platform: the `TradingSession` keeps the authoritative order book, matches orders into transactions, tracks active traders, and publishes market snapshots.
- Messaging (RabbitMQ): acts as the middle‑man for communication. It handles:
  - broadcasts (fanout) of the full market snapshot to all traders
  - individual (direct) messages back to specific traders
- Persistence (MongoDB): stores transactions and broadcast messages when enabled; if Mongo is unavailable, the session continues with in‑memory tracking.

## Data Flow (High Level)

1. A `TraderManager` creates a trading session and launches traders.
2. Each trader registers via RabbitMQ and subscribes to broadcasts.
3. Traders submit `add_order` / `cancel_order` requests to the trading platform.
4. The trading platform updates the order book, matches orders, records transactions, and broadcasts updated state.
5. MongoDB persists messages and transactions when configured.

## Quick Health Checks

The API exposes a status endpoint that verifies RabbitMQ and MongoDB connectivity:

```bash
curl http://localhost:8001/status
```

If you want to check services directly:

- RabbitMQ (local): `rabbitmqctl status`
- MongoDB (local): `mongosh --eval "db.runCommand({ ping: 1 })"`

Mongo can be disabled with `DISABLE_MONGO=1` to run without persistence.

# Human Trader API

## HTTP Endpoints

- `POST /trading/initiate`: starts a new session, returns trader IDs (including a human trader ID).
- `GET /traders/defaults`: returns default creation params for a session.
- `GET /trader/{trader_uuid}`: returns session metadata.
- `GET /trader_info/{trader_uuid}`: returns a trader's cash, shares, and open orders.
- `GET /trading_session/{trading_session_id}`: session overview and human trader info.
- `GET /status`: health check for RabbitMQ and MongoDB.

## WebSocket

Connect a human trader to the live session:

`ws://localhost:8001/trader/{trader_id}`

### Client -> Server Messages

All messages are JSON with a `type` and `data`.

Add order:

```json
{ "type": "add_order", "data": { "type": -1, "price": 1000, "amount": 1 } }
```

- `type`: `-1` for ask, `1` for bid
- `price`: order price
- `amount`: order size (optional, defaults to 1)

Cancel order:

```json
{ "type": "cancel_order", "data": { "id": "<order_uuid>" } }
```

### Server -> Client Messages

On connect:

```json
{ "type": "success", "message": "Connected to trader", "data": { "trader_uuid": "...", "order_book": { "bids": [], "asks": [] } } }
```

Regular updates (broadcasts and direct updates):

- `type: "update"`
  - `order_book`: current aggregated bids/asks
  - `active_orders`: full list of active orders
  - `history`: transaction list (if available)
  - `spread`, `midpoint`, `transaction_price`
  - `new_transactions`: list of executed trades for this trader (only when relevant)

Session close:

- `type: "closure"`

# Agent Specification (Current Implementation)

The current Noise Trader is a simple random agent that places or cancels orders around the live mid‑price.

- Observation: uses the latest order book snapshot (best bid/ask) plus its own active orders.
- Parameters (from `trader_manager.py`):
  - `activity_frequency`: average wait time between actions (exponential).
  - `initial_price`: seed price when the book is empty.
  - `price_step`: minimum price step.
  - `cancel_probability`: chance to cancel a random own order.
  - `max_price_deviation`: max offset (in steps) from mid‑price for new orders.
- Actions:
  - Post a new bid/ask order near the mid‑price.
  - Cancel a random own order.
