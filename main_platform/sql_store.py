import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from sqlalchemy import (
        Boolean,
        Column,
        Float,
        Integer,
        MetaData,
        String,
        Table,
        Text,
        create_engine,
        desc,
        func,
        select,
    )

    SQLALCHEMY_AVAILABLE = True
    SQLALCHEMY_IMPORT_ERROR = ""
except Exception as exc:
    SQLALCHEMY_AVAILABLE = False
    SQLALCHEMY_IMPORT_ERROR = str(exc)


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _iso(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _resolve_database_url() -> str:
    db_url = os.getenv("TRADING_DB_URL", "").strip() or os.getenv("DATABASE_URL", "").strip()
    if db_url:
        if db_url.startswith("postgres://"):
            return "postgresql+psycopg2://" + db_url[len("postgres://") :]
        if db_url.startswith("postgresql://"):
            return db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
        if db_url.startswith("mysql://"):
            return db_url.replace("mysql://", "mysql+pymysql://", 1)
        return db_url

    override = os.getenv("OTREE_BRIDGE_DB_PATH", "").strip()
    if override:
        local_sqlite = Path(override).expanduser().resolve()
    else:
        local_sqlite = (Path(__file__).resolve().parents[2] / "otree_trader_bridge" / "db.sqlite3").resolve()
    return f"sqlite:///{local_sqlite}"


class SQLStore:
    def __init__(self, logger):
        self.logger = logger
        self.db_url = _resolve_database_url()
        self.enabled = False
        self.error = ""
        self.engine = None
        self.metadata = None

        self.trading_sessions = None
        self.traders = None
        self.orders = None
        self.transactions = None
        self.messages = None

        self._debug_lock = threading.Lock()
        self._schema_lock = threading.Lock()
        self._debug_counters = {
            "register_session": {"attempted": 0, "saved": 0, "skipped_disabled": 0, "errors": 0},
            "register_traders": {"attempted": 0, "saved": 0, "skipped_disabled": 0, "errors": 0},
            "save_order": {"attempted": 0, "saved": 0, "skipped_disabled": 0, "errors": 0},
            "save_transaction": {"attempted": 0, "saved": 0, "skipped_disabled": 0, "errors": 0},
            "save_message": {"attempted": 0, "saved": 0, "skipped_disabled": 0, "errors": 0},
        }
        self._debug_recent_events = []
        self._debug_max_events = 200

        if not SQLALCHEMY_AVAILABLE:
            self.error = f"sqlalchemy unavailable: {SQLALCHEMY_IMPORT_ERROR}"
            return

        self.metadata = MetaData()

        self.trading_sessions = Table(
            "trading_platform_sessions",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("trading_session_uuid", String(255), nullable=False),
            Column("num_human_traders", Integer, nullable=True),
            Column("payload_json", Text, nullable=True),
            Column("response_json", Text, nullable=True),
            Column("created_ts", Float, nullable=False),
        )

        self.traders = Table(
            "trading_platform_traders",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("trading_session_uuid", String(255), nullable=False),
            Column("trader_uuid", String(255), nullable=False),
            Column("trader_type", String(64), nullable=True),
            Column("is_human", Boolean, nullable=True),
            Column("initial_cash", Float, nullable=True),
            Column("initial_shares", Float, nullable=True),
            Column("created_ts", Float, nullable=False),
        )

        self.orders = Table(
            "trading_platform_orders",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("trading_session_uuid", String(255), nullable=False),
            Column("trader_uuid", String(255), nullable=True),
            Column("order_id", String(255), nullable=True),
            Column("status", String(64), nullable=True),
            Column("order_type", String(64), nullable=True),
            Column("amount", Float, nullable=True),
            Column("price", Float, nullable=True),
            Column("timestamp", Text, nullable=True),
            Column("order_json", Text, nullable=True),
            Column("created_ts", Float, nullable=False),
        )

        self.transactions = Table(
            "trading_platform_transactions",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("trading_session_uuid", String(255), nullable=False),
            Column("transaction_id", String(255), nullable=True),
            Column("bid_order_id", String(255), nullable=True),
            Column("ask_order_id", String(255), nullable=True),
            Column("bid_trader_uuid", String(255), nullable=True),
            Column("ask_trader_uuid", String(255), nullable=True),
            Column("price", Float, nullable=True),
            Column("timestamp", Text, nullable=True),
            Column("transaction_json", Text, nullable=True),
            Column("created_ts", Float, nullable=False),
        )

        self.messages = Table(
            "trading_platform_messages",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("trading_session_uuid", String(255), nullable=False),
            Column("recipient_trader_uuid", String(255), nullable=True),
            Column("message_type", String(64), nullable=True),
            Column("content_json", Text, nullable=True),
            Column("timestamp", Text, nullable=True),
            Column("created_ts", Float, nullable=False),
        )

    def initialize(self):
        if not SQLALCHEMY_AVAILABLE:
            self.enabled = False
            if not self.error:
                self.error = f"sqlalchemy unavailable: {SQLALCHEMY_IMPORT_ERROR}"
            self.logger.warning(f"SQL persistence disabled: {self.error}")
            return
        try:
            connect_args = {"check_same_thread": False} if self.db_url.startswith("sqlite") else {}
            try:
                self.engine = create_engine(
                    self.db_url,
                    future=True,
                    pool_pre_ping=True,
                    connect_args=connect_args,
                )
            except TypeError:
                self.engine = create_engine(
                    self.db_url,
                    pool_pre_ping=True,
                    connect_args=connect_args,
                )
            with self.engine.begin() as conn:
                conn.exec_driver_sql("SELECT 1")
            self.metadata.create_all(self.engine, checkfirst=True)
            self.enabled = True
            self.error = ""
            self.logger.info(f"SQL persistence enabled: {self.db_url}")
            self.ensure_schema()
        except Exception as exc:
            self.enabled = False
            self.error = str(exc)
            self.logger.warning(f"SQL persistence disabled: {self.error}")

    def healthcheck(self):
        if not self.enabled or not self.engine:
            return False, self.error or "disabled"
        try:
            with self.engine.begin() as conn:
                conn.exec_driver_sql("SELECT 1")
            return True, None
        except Exception as exc:
            return False, str(exc)

    def ensure_schema(self) -> bool:
        if not self.enabled or not self.engine or not self.metadata:
            return False
        try:
            with self._schema_lock:
                self.metadata.create_all(self.engine, checkfirst=True)
            return True
        except Exception as exc:
            self.logger.warning(f"SQL schema bootstrap failed: {exc}")
            return False

    def _debug_inc(self, action: str, field: str, delta: int = 1):
        with self._debug_lock:
            bucket = self._debug_counters.setdefault(action, {})
            bucket[field] = bucket.get(field, 0) + max(0, int(delta))

    def _debug_event(self, action: str, status: str, details: Optional[Dict] = None):
        payload = {
            "ts": _iso(datetime.now(timezone.utc)),
            "action": action,
            "status": status,
            "details": details or {},
        }
        with self._debug_lock:
            self._debug_recent_events.append(payload)
            if len(self._debug_recent_events) > self._debug_max_events:
                self._debug_recent_events = self._debug_recent_events[-self._debug_max_events :]

    def _debug_table_row_counts(self) -> Dict[str, Optional[int]]:
        table_map = {
            "sessions": self.trading_sessions,
            "traders": self.traders,
            "orders": self.orders,
            "transactions": self.transactions,
            "messages": self.messages,
        }
        result = {}
        if not self.enabled or not self.engine:
            for key in table_map:
                result[key] = None
            return result
        try:
            with self.engine.begin() as conn:
                for key, table in table_map.items():
                    stmt = select(func.count()).select_from(table)
                    result[key] = int(conn.execute(stmt).scalar() or 0)
        except Exception as exc:
            self.logger.warning(f"SQL debug table count failed: {exc}")
            for key in table_map:
                result.setdefault(key, None)
        return result

    def debug_snapshot(self) -> Dict:
        with self._debug_lock:
            counters_copy = {k: dict(v) for k, v in self._debug_counters.items()}
            events_copy = list(self._debug_recent_events)
        return {
            "enabled": self.enabled,
            "error": self.error,
            "database_url": self.db_url,
            "counters": counters_copy,
            "recent_events": events_copy,
            "table_row_counts": self._debug_table_row_counts(),
        }

    def register_session(self, trading_session_uuid: str, payload: Dict, response_data: Optional[Dict] = None):
        self._debug_inc("register_session", "attempted")
        if not self.enabled:
            self._debug_inc("register_session", "skipped_disabled")
            self._debug_event("register_session", "skipped_disabled", {"trading_session_uuid": str(trading_session_uuid)})
            return
        if not self.ensure_schema():
            self._debug_inc("register_session", "errors")
            self._debug_event(
                "register_session",
                "error",
                {"trading_session_uuid": str(trading_session_uuid), "error": "schema bootstrap failed"},
            )
            return

        session_id = str(trading_session_uuid or "")
        row = {
            "trading_session_uuid": session_id,
            "num_human_traders": int((payload or {}).get("num_human_traders") or 0),
            "payload_json": _json(payload or {}),
            "response_json": _json(response_data or {}),
            "created_ts": _now_ts(),
        }
        try:
            with self.engine.begin() as conn:
                conn.execute(self.trading_sessions.insert().values(**row))
            self._debug_inc("register_session", "saved")
            self._debug_event("register_session", "saved", {"trading_session_uuid": session_id})
        except Exception as exc:
            self._debug_inc("register_session", "errors")
            self._debug_event(
                "register_session",
                "error",
                {"trading_session_uuid": session_id, "error": str(exc)},
            )
            raise

    def register_traders(self, trading_session_uuid: str, traders: List[Dict]):
        self._debug_inc("register_traders", "attempted")
        if not self.enabled:
            self._debug_inc("register_traders", "skipped_disabled")
            self._debug_event("register_traders", "skipped_disabled", {"trading_session_uuid": str(trading_session_uuid)})
            return
        if not self.ensure_schema():
            self._debug_inc("register_traders", "errors")
            self._debug_event(
                "register_traders",
                "error",
                {"trading_session_uuid": str(trading_session_uuid), "error": "schema bootstrap failed"},
            )
            return

        session_id = str(trading_session_uuid or "")
        trader_rows = traders or []
        try:
            saved = 0
            with self.engine.begin() as conn:
                for trader in trader_rows:
                    trader_uuid = str((trader or {}).get("trader_uuid") or "")
                    if not trader_uuid:
                        continue
                    exists_stmt = (
                        select(self.traders.c.id)
                        .where(self.traders.c.trading_session_uuid == session_id)
                        .where(self.traders.c.trader_uuid == trader_uuid)
                        .order_by(desc(self.traders.c.id))
                        .limit(1)
                    )
                    if conn.execute(exists_stmt).first():
                        continue
                    row = {
                        "trading_session_uuid": session_id,
                        "trader_uuid": trader_uuid,
                        "trader_type": str((trader or {}).get("trader_type") or ""),
                        "is_human": bool((trader or {}).get("is_human") or False),
                        "initial_cash": float((trader or {}).get("initial_cash") or 0),
                        "initial_shares": float((trader or {}).get("initial_shares") or 0),
                        "created_ts": _now_ts(),
                    }
                    conn.execute(self.traders.insert().values(**row))
                    saved += 1
            self._debug_inc("register_traders", "saved", delta=saved)
            self._debug_event(
                "register_traders",
                "saved",
                {"trading_session_uuid": session_id, "saved_rows": saved},
            )
        except Exception as exc:
            self._debug_inc("register_traders", "errors")
            self._debug_event(
                "register_traders",
                "error",
                {"trading_session_uuid": session_id, "error": str(exc)},
            )
            raise

    def save_order(self, trading_session_uuid: str, order: Dict):
        self._debug_inc("save_order", "attempted")
        if not self.enabled:
            self._debug_inc("save_order", "skipped_disabled")
            self._debug_event("save_order", "skipped_disabled", {"trading_session_uuid": str(trading_session_uuid)})
            return
        if not self.ensure_schema():
            self._debug_inc("save_order", "errors")
            self._debug_event(
                "save_order",
                "error",
                {
                    "trading_session_uuid": str(trading_session_uuid),
                    "order_id": str((order or {}).get("id") or ""),
                    "error": "schema bootstrap failed",
                },
            )
            return

        trader_uuid = str((order or {}).get("trader_id") or "")
        row = {
            "trading_session_uuid": str(trading_session_uuid or ""),
            "trader_uuid": trader_uuid,
            "order_id": str((order or {}).get("id") or ""),
            "status": str((order or {}).get("status") or ""),
            "order_type": str((order or {}).get("order_type") or ""),
            "amount": float((order or {}).get("amount") or 0),
            "price": float((order or {}).get("price") or 0),
            "timestamp": _iso((order or {}).get("timestamp")),
            "order_json": _json(order or {}),
            "created_ts": _now_ts(),
        }
        try:
            with self.engine.begin() as conn:
                conn.execute(self.orders.insert().values(**row))
            self._debug_inc("save_order", "saved")
            self._debug_event(
                "save_order",
                "saved",
                {
                    "trading_session_uuid": str(trading_session_uuid or ""),
                    "trader_uuid": trader_uuid,
                    "order_id": row["order_id"],
                },
            )
        except Exception as exc:
            self._debug_inc("save_order", "errors")
            self._debug_event(
                "save_order",
                "error",
                {
                    "trading_session_uuid": str(trading_session_uuid or ""),
                    "trader_uuid": trader_uuid,
                    "order_id": row["order_id"],
                    "error": str(exc),
                },
            )
            raise

    def save_transaction(
        self,
        trading_session_uuid: str,
        bid_trader_uuid: str,
        ask_trader_uuid: str,
        bid_order_id: str,
        ask_order_id: str,
        price: float,
        timestamp,
        transaction_id: str,
        transaction_json: Dict,
    ):
        self._debug_inc("save_transaction", "attempted")
        if not self.enabled:
            self._debug_inc("save_transaction", "skipped_disabled")
            self._debug_event("save_transaction", "skipped_disabled", {"trading_session_uuid": str(trading_session_uuid)})
            return
        if not self.ensure_schema():
            self._debug_inc("save_transaction", "errors")
            self._debug_event(
                "save_transaction",
                "error",
                {
                    "trading_session_uuid": str(trading_session_uuid),
                    "transaction_id": str(transaction_id or ""),
                    "error": "schema bootstrap failed",
                },
            )
            return

        row = {
            "trading_session_uuid": str(trading_session_uuid or ""),
            "transaction_id": str(transaction_id or ""),
            "bid_order_id": str(bid_order_id or ""),
            "ask_order_id": str(ask_order_id or ""),
            "bid_trader_uuid": str(bid_trader_uuid or ""),
            "ask_trader_uuid": str(ask_trader_uuid or ""),
            "price": float(price or 0),
            "timestamp": _iso(timestamp),
            "transaction_json": _json(transaction_json or {}),
            "created_ts": _now_ts(),
        }
        try:
            with self.engine.begin() as conn:
                conn.execute(self.transactions.insert().values(**row))
            self._debug_inc("save_transaction", "saved")
            self._debug_event(
                "save_transaction",
                "saved",
                {
                    "trading_session_uuid": str(trading_session_uuid or ""),
                    "transaction_id": row["transaction_id"],
                },
            )
        except Exception as exc:
            self._debug_inc("save_transaction", "errors")
            self._debug_event(
                "save_transaction",
                "error",
                {
                    "trading_session_uuid": str(trading_session_uuid or ""),
                    "transaction_id": row["transaction_id"],
                    "error": str(exc),
                },
            )
            raise

    def save_message(
        self,
        trading_session_uuid: str,
        message_type: str,
        content: Dict,
        recipient_trader_uuid: Optional[str] = None,
        timestamp=None,
    ):
        self._debug_inc("save_message", "attempted")
        if not self.enabled:
            self._debug_inc("save_message", "skipped_disabled")
            self._debug_event("save_message", "skipped_disabled", {"trading_session_uuid": str(trading_session_uuid)})
            return
        if not self.ensure_schema():
            self._debug_inc("save_message", "errors")
            self._debug_event(
                "save_message",
                "error",
                {
                    "trading_session_uuid": str(trading_session_uuid),
                    "message_type": str(message_type or ""),
                    "error": "schema bootstrap failed",
                },
            )
            return

        row = {
            "trading_session_uuid": str(trading_session_uuid or ""),
            "recipient_trader_uuid": str(recipient_trader_uuid or ""),
            "message_type": str(message_type or ""),
            "content_json": _json(content or {}),
            "timestamp": _iso(timestamp) if timestamp is not None else _iso(datetime.now(timezone.utc)),
            "created_ts": _now_ts(),
        }
        try:
            with self.engine.begin() as conn:
                conn.execute(self.messages.insert().values(**row))
            self._debug_inc("save_message", "saved")
            self._debug_event(
                "save_message",
                "saved",
                {
                    "trading_session_uuid": str(trading_session_uuid or ""),
                    "message_type": row["message_type"],
                    "recipient_trader_uuid": row["recipient_trader_uuid"],
                },
            )
        except Exception as exc:
            self._debug_inc("save_message", "errors")
            self._debug_event(
                "save_message",
                "error",
                {
                    "trading_session_uuid": str(trading_session_uuid or ""),
                    "message_type": row["message_type"],
                    "recipient_trader_uuid": row["recipient_trader_uuid"],
                    "error": str(exc),
                },
            )
            raise
