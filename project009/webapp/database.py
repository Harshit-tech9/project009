"""SQLite persistence for Phase 1 assessment state and replayable events."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: str | None, default: Any) -> Any:
    return json.loads(value) if value else default


WAITING_ACTIVITIES = {
    "confirm_research": "reviewing_research",
    "answer_interview": "awaiting_interview_answer",
    "select_opportunity": "selecting_opportunity",
    "confirm_blueprint": "reviewing_blueprint",
    "proof_workspace": "awaiting_proof_choice",
}


def running_activity(action_type: str, response: dict[str, Any]) -> str:
    if action_type == "confirm_research":
        return "preparing_interview"
    if action_type == "answer_interview":
        return "preparing_question"
    if action_type == "select_opportunity":
        return "preparing_blueprint"
    if action_type == "confirm_blueprint":
        return "preparing_proof"
    if action_type == "proof_workspace":
        return {
            "message": "answering_proof_message",
            "run": "analyzing_documents",
            "skip": "preparing_results",
        }.get(str(response.get("kind")), "awaiting_proof_choice")
    return "running"


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS assessments (
  assessment_id TEXT PRIMARY KEY,
  company_key TEXT NOT NULL,
  company_name TEXT NOT NULL,
  status TEXT NOT NULL,
  current_stage TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  budget_credit_micros INTEGER NOT NULL,
  results_json TEXT NOT NULL DEFAULT '{}',
  total_input_tokens INTEGER NOT NULL DEFAULT 0,
  total_output_tokens INTEGER NOT NULL DEFAULT 0,
  known_cost_usd REAL NOT NULL DEFAULT 0,
  unreported_cost INTEGER NOT NULL DEFAULT 0,
  failure_reason TEXT,
  cleanup_error TEXT,
  instance_id TEXT
);
CREATE TABLE IF NOT EXISTS pending_actions (
  action_id TEXT PRIMARY KEY,
  assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  stage TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,
  response_json TEXT,
  created_at TEXT NOT NULL,
  accepted_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_actions_current
ON pending_actions(assessment_id) WHERE status = 'pending';
CREATE TABLE IF NOT EXISTS events (
  assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id) ON DELETE CASCADE,
  stream_sequence INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (assessment_id, stream_sequence)
);
CREATE TABLE IF NOT EXISTS documents (
  document_id TEXT PRIMARY KEY,
  assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id) ON DELETE CASCADE,
  original_name TEXT NOT NULL,
  stored_path TEXT NOT NULL,
  format TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  consumed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_documents_assessment
ON documents(assessment_id);
"""
MIGRATIONS = {
    1: SCHEMA,
    2: "ALTER TABLE assessments ADD COLUMN requirements_json TEXT NOT NULL DEFAULT '{}';",
    3: """
    ALTER TABLE assessments ADD COLUMN activity TEXT NOT NULL DEFAULT 'queued';
    ALTER TABLE assessments ADD COLUMN proof_status TEXT NOT NULL DEFAULT 'not_run';
    CREATE TABLE proof_messages (
      message_id TEXT PRIMARY KEY,
      assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id) ON DELETE CASCADE,
      sequence INTEGER NOT NULL,
      role TEXT NOT NULL,
      content TEXT NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE (assessment_id, sequence)
    );
    CREATE INDEX idx_proof_messages_assessment
    ON proof_messages(assessment_id, sequence);
    """,
}


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def _connect(self) -> aiosqlite.Connection:
        connection = await aiosqlite.connect(self.path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    async def initialize(self) -> None:
        async with self._lock:
            connection = await self._connect()
            try:
                await connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                cursor = await connection.execute(
                    "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
                )
                current = int((await cursor.fetchone())["version"])
                for version, sql in sorted(MIGRATIONS.items()):
                    if version <= current:
                        continue
                    await connection.executescript(sql)
                    await connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (version, utc_now()),
                    )
                await connection.execute("PRAGMA optimize")
                await connection.commit()
            finally:
                await connection.close()

    async def create_assessment(
        self,
        assessment_id: str,
        company_key: str,
        company_name: str,
        budget_credit_micros: int,
        requirements: dict[str, Any] | None = None,
    ) -> None:
        created_at = utc_now()
        async with self._lock:
            connection = await self._connect()
            try:
                await connection.execute(
                    """INSERT INTO assessments
                    (assessment_id, company_key, company_name, status, current_stage,
                     created_at, budget_credit_micros, requirements_json)
                    VALUES (?, ?, ?, 'queued', 'start', ?, ?, ?)""",
                    (
                        assessment_id,
                        company_key,
                        company_name,
                        created_at,
                        budget_credit_micros,
                        _json(requirements or {}),
                    ),
                )
                await self._append_event(
                    connection,
                    assessment_id,
                    "snapshot",
                    {"status": "queued", "stage": "start"},
                )
                await connection.commit()
            finally:
                await connection.close()

    async def get_assessment_row(self, assessment_id: str) -> dict[str, Any] | None:
        connection = await self._connect()
        try:
            cursor = await connection.execute(
                "SELECT * FROM assessments WHERE assessment_id = ?", (assessment_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None
        finally:
            await connection.close()

    async def set_lifecycle(
        self,
        assessment_id: str,
        status: str,
        *,
        stage: str | None = None,
        failure_reason: str | None = None,
        cleanup_error: str | None = None,
        instance_id: str | None = None,
        activity: str | None = None,
        proof_status: str | None = None,
        terminal: bool = False,
    ) -> None:
        fields = ["status = ?"]
        values: list[Any] = [status]
        if stage is not None:
            fields.append("current_stage = ?")
            values.append(stage)
        if failure_reason is not None:
            fields.append("failure_reason = ?")
            values.append(failure_reason)
        if cleanup_error is not None:
            fields.append("cleanup_error = ?")
            values.append(cleanup_error)
        if instance_id is not None:
            fields.append("instance_id = ?")
            values.append(instance_id)
        if activity is not None:
            fields.append("activity = ?")
            values.append(activity)
        if proof_status is not None:
            fields.append("proof_status = ?")
            values.append(proof_status)
        if status == "running":
            fields.append("started_at = COALESCE(started_at, ?)")
            values.append(utc_now())
        if terminal:
            fields.append("completed_at = ?")
            values.append(utc_now())
            fields.append("activity = ?")
            values.append(status)
        values.append(assessment_id)
        async with self._lock:
            connection = await self._connect()
            try:
                await connection.execute(
                    f"UPDATE assessments SET {', '.join(fields)} WHERE assessment_id = ?",
                    values,
                )
                await connection.commit()
            finally:
                await connection.close()

    async def project_result(self, assessment_id: str, key: str, value: Any) -> None:
        async with self._lock:
            connection = await self._connect()
            try:
                cursor = await connection.execute(
                    "SELECT results_json FROM assessments WHERE assessment_id = ?",
                    (assessment_id,),
                )
                row = await cursor.fetchone()
                if row is None:
                    return
                results = _load(row["results_json"], {})
                results[key] = value
                await connection.execute(
                    "UPDATE assessments SET results_json = ? WHERE assessment_id = ?",
                    (_json(results), assessment_id),
                )
                await connection.commit()
            finally:
                await connection.close()

    async def project_final(self, assessment_id: str, assessment: dict[str, Any]) -> None:
        results = {
            key: assessment.get(key)
            for key in ("research", "research_corrections", "interview", "opportunities", "blueprint", "proof")
            if assessment.get(key) is not None
        }
        async with self._lock:
            connection = await self._connect()
            try:
                await connection.execute(
                    """UPDATE assessments SET results_json = ?, total_input_tokens = ?,
                    total_output_tokens = ?, known_cost_usd = ?, unreported_cost = ?,
                    failure_reason = ?, cleanup_error = ?, proof_status = ?
                    WHERE assessment_id = ?""",
                    (
                        _json(results),
                        assessment.get("total_input_tokens", 0),
                        assessment.get("total_output_tokens", 0),
                        assessment.get("known_cost_usd", 0),
                        int(bool(assessment.get("unreported_cost"))),
                        assessment.get("failure_reason"),
                        assessment.get("cleanup_error"),
                        assessment.get("proof_status", "not_run"),
                        assessment_id,
                    ),
                )
                await connection.commit()
            finally:
                await connection.close()

    async def _append_event(
        self,
        connection: aiosqlite.Connection,
        assessment_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> int:
        cursor = await connection.execute(
            "SELECT COALESCE(MAX(stream_sequence), 0) + 1 AS next FROM events WHERE assessment_id = ?",
            (assessment_id,),
        )
        row = await cursor.fetchone()
        sequence = int(row["next"])
        await connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
            (assessment_id, sequence, event_type, _json(payload), utc_now()),
        )
        return sequence

    async def append_event(
        self, assessment_id: str, event_type: str, payload: dict[str, Any]
    ) -> int:
        async with self._lock:
            connection = await self._connect()
            try:
                await connection.execute("BEGIN IMMEDIATE")
                sequence = await self._append_event(
                    connection, assessment_id, event_type, payload
                )
                await connection.commit()
                return sequence
            finally:
                await connection.close()

    async def fetch_events(self, assessment_id: str, after: int) -> list[dict[str, Any]]:
        connection = await self._connect()
        try:
            cursor = await connection.execute(
                """SELECT stream_sequence, event_type, payload_json, created_at
                FROM events WHERE assessment_id = ? AND stream_sequence > ?
                ORDER BY stream_sequence""",
                (assessment_id, after),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "sequence": row["stream_sequence"],
                    "event_type": row["event_type"],
                    "payload": _load(row["payload_json"], {}),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]
        finally:
            await connection.close()

    async def create_action(
        self,
        action_id: str,
        assessment_id: str,
        action_type: str,
        stage: str,
        payload: dict[str, Any],
    ) -> None:
        now = utc_now()
        async with self._lock:
            connection = await self._connect()
            try:
                await connection.execute("BEGIN IMMEDIATE")
                await connection.execute(
                    """INSERT INTO pending_actions
                    (action_id, assessment_id, type, stage, payload_json, status, created_at)
                    VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                    (action_id, assessment_id, action_type, stage, _json(payload), now),
                )
                await connection.execute(
                    """UPDATE assessments SET status = 'waiting_for_user',
                    current_stage = ?, activity = ? WHERE assessment_id = ?""",
                    (stage, WAITING_ACTIVITIES.get(action_type, "waiting_for_user"), assessment_id),
                )
                await self._append_event(
                    connection,
                    assessment_id,
                    "interaction_required",
                    {
                        "action_id": action_id,
                        "type": action_type,
                        "stage": stage,
                        "payload": payload,
                        "created_at": now,
                    },
                )
                await connection.commit()
            finally:
                await connection.close()

    async def accept_action(
        self, assessment_id: str, action_id: str, response: dict[str, Any]
    ) -> tuple[str, dict[str, Any] | None]:
        async with self._lock:
            connection = await self._connect()
            try:
                await connection.execute("BEGIN IMMEDIATE")
                cursor = await connection.execute(
                    "SELECT * FROM pending_actions WHERE action_id = ? AND assessment_id = ?",
                    (action_id, assessment_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    await connection.rollback()
                    return "missing", None
                prior = _load(row["response_json"], None)
                if row["status"] == "accepted":
                    await connection.rollback()
                    return ("duplicate" if prior == response else "conflict"), prior
                if row["status"] != "pending":
                    await connection.rollback()
                    return "conflict", prior
                cursor = await connection.execute(
                    "SELECT action_id FROM pending_actions WHERE assessment_id = ? AND status = 'pending'",
                    (assessment_id,),
                )
                current = await cursor.fetchone()
                if current is None or current["action_id"] != action_id:
                    await connection.rollback()
                    return "conflict", None
                if row["type"] == "proof_workspace" and response.get("kind") == "run":
                    document_ids = response.get("document_ids", [])
                    placeholders = ",".join("?" for _ in document_ids)
                    cursor = await connection.execute(
                        f"""SELECT document_id, format FROM documents
                        WHERE assessment_id = ? AND consumed = 0
                        AND document_id IN ({placeholders})""",
                        [assessment_id, *document_ids],
                    )
                    documents = await cursor.fetchall()
                    if len(documents) != 3 or {item["format"] for item in documents} != {
                        "pdf", "xlsx", "png"
                    }:
                        await connection.rollback()
                        raise ValueError(
                            "selected documents must contain one owned PDF, XLSX, and PNG"
                        )
                    await connection.execute(
                        f"""UPDATE documents SET consumed = 1
                        WHERE assessment_id = ? AND document_id IN ({placeholders})""",
                        [assessment_id, *document_ids],
                    )
                now = utc_now()
                await connection.execute(
                    """UPDATE pending_actions SET status = 'accepted', response_json = ?, accepted_at = ?
                    WHERE action_id = ?""",
                    (_json(response), now, action_id),
                )
                await connection.execute(
                    "UPDATE assessments SET status = 'running', activity = ? WHERE assessment_id = ?",
                    (running_activity(row["type"], response), assessment_id),
                )
                await self._append_event(
                    connection,
                    assessment_id,
                    "interaction_accepted",
                    {"action_id": action_id, "type": row["type"], "stage": row["stage"]},
                )
                await connection.commit()
                return "accepted", response
            finally:
                await connection.close()

    async def expire_action(self, assessment_id: str, action_id: str) -> None:
        async with self._lock:
            connection = await self._connect()
            try:
                await connection.execute(
                    "UPDATE pending_actions SET status = 'expired' WHERE assessment_id = ? AND action_id = ? AND status = 'pending'",
                    (assessment_id, action_id),
                )
                await connection.commit()
            finally:
                await connection.close()

    async def pending_action(self, assessment_id: str) -> dict[str, Any] | None:
        connection = await self._connect()
        try:
            cursor = await connection.execute(
                "SELECT * FROM pending_actions WHERE assessment_id = ? AND status = 'pending'",
                (assessment_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return {
                "action_id": row["action_id"],
                "type": row["type"],
                "stage": row["stage"],
                "payload": _load(row["payload_json"], {}),
                "created_at": row["created_at"],
            }
        finally:
            await connection.close()

    async def add_document(self, document: dict[str, Any]) -> None:
        async with self._lock:
            connection = await self._connect()
            try:
                await connection.execute(
                    """INSERT INTO documents
                    (document_id, assessment_id, original_name, stored_path, format,
                     mime_type, size_bytes, sha256, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        document["document_id"], document["assessment_id"],
                        document["original_name"], document["stored_path"], document["format"],
                        document["mime_type"], document["size_bytes"], document["sha256"], utc_now(),
                    ),
                )
                await connection.commit()
            finally:
                await connection.close()

    async def list_documents(self, assessment_id: str) -> list[dict[str, Any]]:
        connection = await self._connect()
        try:
            cursor = await connection.execute(
                "SELECT * FROM documents WHERE assessment_id = ? ORDER BY created_at",
                (assessment_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]
        finally:
            await connection.close()

    async def delete_document(self, assessment_id: str, document_id: str) -> dict[str, Any] | None:
        async with self._lock:
            connection = await self._connect()
            try:
                cursor = await connection.execute(
                    "SELECT * FROM documents WHERE assessment_id = ? AND document_id = ? AND consumed = 0",
                    (assessment_id, document_id),
                )
                row = await cursor.fetchone()
                if row:
                    await connection.execute(
                        "DELETE FROM documents WHERE document_id = ?", (document_id,)
                    )
                    await connection.commit()
                    return dict(row)
                return None
            finally:
                await connection.close()

    async def mark_documents_consumed(self, document_ids: list[str]) -> None:
        if not document_ids:
            return
        placeholders = ",".join("?" for _ in document_ids)
        async with self._lock:
            connection = await self._connect()
            try:
                await connection.execute(
                    f"UPDATE documents SET consumed = 1 WHERE document_id IN ({placeholders})",
                    document_ids,
                )
                await connection.commit()
            finally:
                await connection.close()

    async def add_proof_message(
        self, assessment_id: str, message_id: str, role: str, content: str
    ) -> dict[str, Any]:
        async with self._lock:
            connection = await self._connect()
            try:
                await connection.execute("BEGIN IMMEDIATE")
                cursor = await connection.execute(
                    """SELECT COALESCE(MAX(sequence), 0) + 1 AS next
                    FROM proof_messages WHERE assessment_id = ?""",
                    (assessment_id,),
                )
                sequence = int((await cursor.fetchone())["next"])
                created_at = utc_now()
                await connection.execute(
                    """INSERT INTO proof_messages
                    (message_id, assessment_id, sequence, role, content, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (message_id, assessment_id, sequence, role, content, created_at),
                )
                await connection.commit()
                return {
                    "message_id": message_id,
                    "sequence": sequence,
                    "role": role,
                    "content": content,
                    "created_at": created_at,
                }
            finally:
                await connection.close()

    async def list_proof_messages(self, assessment_id: str) -> list[dict[str, Any]]:
        connection = await self._connect()
        try:
            cursor = await connection.execute(
                """SELECT message_id, sequence, role, content, created_at
                FROM proof_messages WHERE assessment_id = ? ORDER BY sequence""",
                (assessment_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]
        finally:
            await connection.close()

    async def snapshot(self, assessment_id: str) -> dict[str, Any] | None:
        row = await self.get_assessment_row(assessment_id)
        if row is None:
            return None
        events = await self.fetch_events(assessment_id, 0)
        pending = await self.pending_action(assessment_id)
        documents = await self.list_documents(assessment_id)
        proof_messages = await self.list_proof_messages(assessment_id)
        return {
            "assessment_id": row["assessment_id"],
            "company_key": row["company_key"],
            "company_name": row["company_name"],
            "requirements": _load(row.get("requirements_json"), {}),
            "status": row["status"],
            "current_stage": row["current_stage"],
            "activity": row.get("activity", "queued"),
            "proof_status": row.get("proof_status", "not_run"),
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "budget_credit_micros": row["budget_credit_micros"],
            "results": _load(row["results_json"], {}),
            "pending_action": pending,
            "proof_messages": proof_messages,
            "documents": [
                {
                    "document_id": item["document_id"],
                    "name": item["original_name"],
                    "format": item["format"],
                    "mime_type": item["mime_type"],
                    "size_bytes": item["size_bytes"],
                    "sha256": item["sha256"],
                }
                for item in documents
            ],
            "usage": {
                "input_tokens": row["total_input_tokens"],
                "output_tokens": row["total_output_tokens"],
                "known_cost_usd": row["known_cost_usd"],
                "unreported_cost": bool(row["unreported_cost"]),
            },
            "failure_reason": row["failure_reason"],
            "last_event_sequence": events[-1]["sequence"] if events else 0,
        }

    async def recovery_records(self) -> tuple[list[str], list[dict[str, Any]]]:
        connection = await self._connect()
        try:
            queued_cursor = await connection.execute(
                "SELECT assessment_id FROM assessments WHERE status = 'queued'"
            )
            queued = [row["assessment_id"] for row in await queued_cursor.fetchall()]
            active_cursor = await connection.execute(
                "SELECT assessment_id, instance_id FROM assessments WHERE status IN ('running','waiting_for_user','cancelling')"
            )
            active = [dict(row) for row in await active_cursor.fetchall()]
        finally:
            await connection.close()
        for row in active:
            await self.set_lifecycle(
                row["assessment_id"], "failed", stage="results",
                failure_reason="server_restarted", terminal=True,
            )
            await self.append_event(
                row["assessment_id"], "terminal",
                {"status": "failed", "stage": "results", "failure_reason": "server_restarted"},
            )
        return queued, active
