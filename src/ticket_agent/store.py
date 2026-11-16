"""SQLite + fixture store. Refunds start pending; only the approval API can change that."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from ticket_agent.config import data_dir, get_settings
from ticket_agent.schemas import Order, Policy, Refund, RefundStatus, Ticket

HK = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(HK).isoformat(timespec="seconds")


def _row_to_ticket(row: sqlite3.Row) -> Ticket:
    return Ticket(
        id=row["id"],
        subject=row["subject"],
        body=row["body"],
        created_at=row["created_at"],
        customer_first_name=row["customer_first_name"],
        order_id=row["order_id"],
        eval=json.loads(row["eval_json"] or "{}"),
    )


def _row_to_order(row: sqlite3.Row) -> Order:
    return Order(
        id=row["id"],
        status=row["status"],
        last_checkpoint=row["last_checkpoint"],
        eta=row["eta"],
        delivered_at=row["delivered_at"],
        item_summary=row["item_summary"],
        amount_hkd=row["amount_hkd"],
        currency=row["currency"],
        customer_first_name=row["customer_first_name"],
        payment_note=row["payment_note"],
    )


def _row_to_refund(row: sqlite3.Row) -> Refund:
    return Refund(
        id=row["id"],
        ticket_id=row["ticket_id"],
        order_id=row["order_id"],
        amount_hkd=row["amount_hkd"],
        reason=row["reason"],
        status=RefundStatus(row["status"]),
        requested_at=row["requested_at"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
        run_id=row["run_id"],
    )


class Store:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        cur = self._conn
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                customer_first_name TEXT,
                order_id TEXT,
                eval_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                last_checkpoint TEXT,
                eta TEXT,
                delivered_at TEXT,
                item_summary TEXT NOT NULL,
                amount_hkd REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'HKD',
                customer_first_name TEXT NOT NULL,
                payment_note TEXT
            );
            CREATE TABLE IF NOT EXISTS policies (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                body TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS refunds (
                id TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL,
                order_id TEXT NOT NULL,
                amount_hkd REAL NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                decided_at TEXT,
                decided_by TEXT,
                run_id TEXT
            );
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                latency_ms INTEGER,
                step_count INTEGER,
                stop_reason TEXT,
                classification TEXT,
                draft_reply TEXT,
                refund_id TEXT,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                cached_tokens INTEGER DEFAULT 0,
                error TEXT,
                model TEXT
            );
            """
        )
        cur.commit()

    def seed_from_fixtures(self, fixture_dir: Optional[Path] = None, reset_operational: bool = False) -> None:
        """Load tickets, orders, and policies. Optionally wipe refunds and runs."""
        fixture_dir = fixture_dir or data_dir()
        with self._lock:
            if reset_operational:
                self._conn.execute("DELETE FROM refunds")
                self._conn.execute("DELETE FROM runs")
            self._replace_json_table("tickets", fixture_dir / "tickets.json", self._insert_ticket)
            self._replace_json_table("orders", fixture_dir / "orders.json", self._insert_order)
            self._replace_json_table("policies", fixture_dir / "policies.json", self._insert_policy)
            self._conn.commit()

    def _replace_json_table(self, table: str, path: Path, inserter) -> None:
        records = json.loads(path.read_text(encoding="utf-8"))
        self._conn.execute(f"DELETE FROM {table}")
        for item in records:
            inserter(item)

    def _insert_ticket(self, item: dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT INTO tickets (id, subject, body, created_at, customer_first_name, order_id, eval_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                item["id"],
                item["subject"],
                item["body"],
                item["created_at"],
                item.get("customer_first_name"),
                item.get("order_id"),
                json.dumps(item.get("eval") or {}),
            ),
        )

    def _insert_order(self, item: dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT INTO orders (id, status, last_checkpoint, eta, delivered_at, item_summary,
               amount_hkd, currency, customer_first_name, payment_note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item["id"],
                item["status"],
                item.get("last_checkpoint"),
                item.get("eta"),
                item.get("delivered_at"),
                item["item_summary"],
                item["amount_hkd"],
                item.get("currency", "HKD"),
                item["customer_first_name"],
                item.get("payment_note"),
            ),
        )

    def _insert_policy(self, item: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO policies (id, title, body) VALUES (?, ?, ?)",
            (item["id"], item["title"], item["body"]),
        )

    def list_tickets(self) -> list[Ticket]:
        rows = self._conn.execute("SELECT * FROM tickets ORDER BY created_at, id").fetchall()
        return [_row_to_ticket(r) for r in rows]

    def get_ticket(self, ticket_id: str) -> Optional[Ticket]:
        row = self._conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        return _row_to_ticket(row) if row else None

    def list_orders(self) -> list[Order]:
        rows = self._conn.execute("SELECT * FROM orders ORDER BY id").fetchall()
        return [_row_to_order(r) for r in rows]

    def get_order(self, order_id: str) -> Optional[Order]:
        row = self._conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return _row_to_order(row) if row else None

    def get_policy(self, topic: str) -> Optional[Policy]:
        topic = (topic or "").strip().lower()
        if topic in {"all", "*"}:
            return None
        row = self._conn.execute("SELECT * FROM policies WHERE id = ?", (topic,)).fetchone()
        return Policy(id=row["id"], title=row["title"], body=row["body"]) if row else None

    def list_policies(self) -> list[Policy]:
        rows = self._conn.execute("SELECT * FROM policies ORDER BY id").fetchall()
        return [Policy(id=r["id"], title=r["title"], body=r["body"]) for r in rows]

    def request_refund(
        self,
        ticket_id: str,
        order_id: str,
        amount_hkd: float,
        reason: str,
        run_id: Optional[str] = None,
        **_ignored: Any,
    ) -> Refund:
        """Always creates a pending refund. Any smuggled status/approval flag is ignored."""
        refund = Refund(
            id=f"RF-{uuid.uuid4().hex[:8].upper()}",
            ticket_id=ticket_id,
            order_id=order_id,
            amount_hkd=float(amount_hkd),
            reason=reason,
            status=RefundStatus.pending,
            requested_at=_now(),
            run_id=run_id,
        )
        with self._lock:
            self._conn.execute(
                """INSERT INTO refunds (id, ticket_id, order_id, amount_hkd, reason, status,
                   requested_at, decided_at, decided_by, run_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)""",
                (
                    refund.id,
                    refund.ticket_id,
                    refund.order_id,
                    refund.amount_hkd,
                    refund.reason,
                    RefundStatus.pending.value,
                    refund.requested_at,
                    run_id,
                ),
            )
            self._conn.commit()
        return refund

    def get_refund(self, refund_id: str) -> Optional[Refund]:
        row = self._conn.execute("SELECT * FROM refunds WHERE id = ?", (refund_id,)).fetchone()
        return _row_to_refund(row) if row else None

    def list_refunds(self, ticket_id: Optional[str] = None, status: Optional[str] = None) -> list[Refund]:
        sql = "SELECT * FROM refunds WHERE 1=1"
        params: list[Any] = []
        if ticket_id:
            sql += " AND ticket_id = ?"
            params.append(ticket_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY requested_at DESC"
        return [_row_to_refund(r) for r in self._conn.execute(sql, params).fetchall()]

    def decide_refund(self, refund_id: str, status: RefundStatus, actor: str) -> Refund:
        if status not in {RefundStatus.approved, RefundStatus.rejected}:
            raise ValueError("A refund decision must be approved or rejected.")
        if not actor or not actor.strip():
            raise ValueError("A human actor name is required to decide a refund.")
        with self._lock:
            row = self._conn.execute("SELECT * FROM refunds WHERE id = ?", (refund_id,)).fetchone()
            if not row:
                raise KeyError(f"Unknown refund {refund_id}")
            current = RefundStatus(row["status"])
            if current != RefundStatus.pending:
                raise ValueError(f"Refund {refund_id} is already {current.value}.")
            decided_at = _now()
            self._conn.execute(
                "UPDATE refunds SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
                (status.value, decided_at, actor.strip(), refund_id),
            )
            self._conn.commit()
        refund = self.get_refund(refund_id)
        assert refund is not None
        return refund

    def approve_refund(self, refund_id: str, actor: str) -> Refund:
        return self.decide_refund(refund_id, RefundStatus.approved, actor)

    def reject_refund(self, refund_id: str, actor: str) -> Refund:
        return self.decide_refund(refund_id, RefundStatus.rejected, actor)

    def save_run(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO runs (
                    id, ticket_id, started_at, finished_at, latency_ms, step_count, stop_reason,
                    classification, draft_reply, refund_id, prompt_tokens, completion_tokens,
                    total_tokens, cached_tokens, error, model
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["ticket_id"],
                    record["started_at"],
                    record.get("finished_at"),
                    record.get("latency_ms"),
                    record.get("step_count"),
                    record.get("stop_reason"),
                    record.get("classification"),
                    record.get("draft_reply"),
                    record.get("refund_id"),
                    record.get("prompt_tokens", 0),
                    record.get("completion_tokens", 0),
                    record.get("total_tokens", 0),
                    record.get("cached_tokens", 0),
                    record.get("error"),
                    record.get("model"),
                ),
            )
            self._conn.commit()

    def list_runs(self, ticket_id: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM runs"
        params: list[Any] = []
        if ticket_id:
            sql += " WHERE ticket_id = ?"
            params.append(ticket_id)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def latest_run_for_ticket(self, ticket_id: str) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE ticket_id = ? ORDER BY started_at DESC LIMIT 1",
            (ticket_id,),
        ).fetchone()
        return dict(row) if row else None


_STORE: Optional[Store] = None
_STORE_LOCK = threading.Lock()


def get_store() -> Store:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            settings = get_settings()
            _STORE = Store(settings.db_path)
            _STORE.seed_from_fixtures()
        return _STORE


def reset_store_singleton() -> None:
    global _STORE
    with _STORE_LOCK:
        if _STORE is not None:
            _STORE.close()
            _STORE = None
