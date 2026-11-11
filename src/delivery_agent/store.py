"""SQLite persistence for deliveries and webhook events."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from delivery_agent.errors import DeliveryNotFound
from delivery_agent.models import Delivery, DeliveryCreate, initial_stages
from delivery_agent.util import clip_text, utcnow


class DeliveryStore:
    def __init__(self, path: str) -> None:
        self.path = path
        parent = Path(path).parent
        if str(parent) not in {"", "."}:
            parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS webhook_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                """
            )
            self._conn.commit()

    def create(self, data: DeliveryCreate) -> Delivery:
        now = utcnow()
        delivery = Delivery(
            id=f"dlv_{uuid4().hex[:12]}",
            repo=data.repo,
            ref=data.ref,
            product_name=data.product_name,
            target_environment=data.target_environment,
            instruction=data.instruction,
            status="pending",
            stages=initial_stages(),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._insert(delivery)
            self._conn.commit()
        return delivery

    def get(self, delivery_id: str) -> Delivery:
        delivery = self.get_optional(delivery_id)
        if delivery is None:
            raise DeliveryNotFound(delivery_id)
        return delivery

    def get_optional(self, delivery_id: str) -> Delivery | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM deliveries WHERE id = ?",
                (delivery_id,),
            ).fetchone()
        if row is None:
            return None
        return Delivery.model_validate_json(row["payload"])

    def list_deliveries(self, limit: int = 50) -> list[Delivery]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT payload FROM deliveries
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [Delivery.model_validate_json(row["payload"]) for row in rows]

    def update(self, delivery_id: str, mutator: Callable[[Delivery], None]) -> Delivery:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM deliveries WHERE id = ?",
                (delivery_id,),
            ).fetchone()
            if row is None:
                raise DeliveryNotFound(delivery_id)
            delivery = Delivery.model_validate_json(row["payload"])
            mutator(delivery)
            delivery.updated_at = utcnow()
            self._write(delivery)
            self._conn.commit()
            return delivery

    def mark_running(self, delivery_id: str) -> bool:
        with self._lock:
            delivery = self.get(delivery_id)
            if delivery.status != "pending":
                return False
            delivery.status = "running"
            delivery.updated_at = utcnow()
            self._write(delivery)
            self._conn.commit()
            return True

    def add_webhook(
        self,
        source: str,
        summary: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        received_at = utcnow()
        encoded = clip_text(json.dumps(payload, ensure_ascii=False, default=str), limit=20000)
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO webhook_events (source, received_at, summary, payload)
                VALUES (?, ?, ?, ?)
                """,
                (source, received_at, summary, encoded),
            )
            self._conn.commit()
            event_id = int(cursor.lastrowid)
        return {
            "id": event_id,
            "source": source,
            "received_at": received_at,
            "summary": summary,
        }

    def list_webhooks(self, limit: int = 50) -> list[dict[str, object]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, source, received_at, summary FROM webhook_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "source": row["source"],
                "received_at": row["received_at"],
                "summary": row["summary"],
            }
            for row in rows
        ]

    def _insert(self, delivery: Delivery) -> None:
        self._conn.execute(
            """
            INSERT INTO deliveries (id, status, product_name, created_at, payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                delivery.id,
                delivery.status,
                delivery.product_name,
                delivery.created_at,
                delivery.model_dump_json(),
            ),
        )

    def _write(self, delivery: Delivery) -> None:
        self._conn.execute(
            """
            UPDATE deliveries
            SET status = ?, product_name = ?, payload = ?
            WHERE id = ?
            """,
            (
                delivery.status,
                delivery.product_name,
                delivery.model_dump_json(),
                delivery.id,
            ),
        )
