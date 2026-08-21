"""Async SQLite persistence for file context and incidents."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS file_context (
    file_path TEXT PRIMARY KEY,
    function_name TEXT,
    error_count INTEGER DEFAULT 0,
    last_error TEXT,
    last_commit_sha TEXT,
    generated_tests TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS incident_log (
    incident_id TEXT PRIMARY KEY,
    trace_id TEXT UNIQUE,
    commit_sha TEXT,
    user_action TEXT,
    log_trace TEXT,
    file_path TEXT,
    line_number INTEGER,
    root_cause TEXT,
    severity TEXT DEFAULT 'LOW',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS webhook_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT,
    trace_id TEXT,
    kind TEXT NOT NULL,
    success INTEGER NOT NULL,
    detail TEXT,
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS webhook_retry_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT,
    trace_id TEXT,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    attempt INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 5,
    status TEXT DEFAULT 'pending',
    last_error TEXT,
    next_attempt_at TEXT,
    created_at TEXT,
    updated_at TEXT
);
"""

_RETRY_BASE_DELAY_SECONDS = 30

_MIGRATE_SEVERITY = (
    "ALTER TABLE incident_log ADD COLUMN severity TEXT DEFAULT 'LOW'"
)
_MIGRATE_SNOOZE = (
    "ALTER TABLE incident_log ADD COLUMN snoozed_until TEXT DEFAULT NULL"
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ContextStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)
            for migration in (_MIGRATE_SEVERITY, _MIGRATE_SNOOZE):
                try:
                    await db.execute(migration)
                except Exception:  # column already exists
                    pass
            await db.commit()

    async def upsert_file_context(
        self,
        file_path: str,
        function_name: str,
        error_info: dict[str, Any],
        tests: list[str],
        *,
        bump_error_count: bool = True,
    ) -> None:
        """Merge file context. When bump_error_count is False, only merges tests/metadata."""
        now = _utc_now_iso()
        last_error_json = json.dumps(error_info, ensure_ascii=False)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT error_count, generated_tests FROM file_context WHERE file_path = ?",
                (file_path,),
            )
            row = await cur.fetchone()
            if row is None:
                merged_tests = list(tests)
                err_count = 1 if bump_error_count else 0
                await db.execute(
                    """
                    INSERT INTO file_context (
                        file_path, function_name, error_count, last_error,
                        last_commit_sha, generated_tests, updated_at
                    ) VALUES (?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        file_path,
                        function_name,
                        err_count,
                        last_error_json,
                        json.dumps(merged_tests, ensure_ascii=False),
                        now,
                    ),
                )
            else:
                err_count = int(row["error_count"])
                if bump_error_count:
                    err_count += 1
                existing_tests: list[str] = []
                if row["generated_tests"]:
                    try:
                        existing_tests = json.loads(row["generated_tests"])
                    except json.JSONDecodeError:
                        existing_tests = []
                merged_tests = existing_tests + [t for t in tests if t]
                await db.execute(
                    """
                    UPDATE file_context SET
                        function_name = ?,
                        error_count = ?,
                        last_error = ?,
                        generated_tests = ?,
                        updated_at = ?
                    WHERE file_path = ?
                    """,
                    (
                        function_name,
                        err_count,
                        last_error_json,
                        json.dumps(merged_tests, ensure_ascii=False),
                        now,
                        file_path,
                    ),
                )
            await db.commit()

    async def incident_exists_for_signature(
        self, file_path: str, line_number: int, error_type: str
    ) -> bool:
        """True if an incident with the same file+line+error already exists (root_cause LIKE)."""
        if not error_type:
            return False
        pattern = f"%{error_type}%"
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                """
                SELECT 1 FROM incident_log
                WHERE file_path = ? AND line_number = ? AND root_cause LIKE ?
                LIMIT 1
                """,
                (file_path, int(line_number), pattern),
            )
            row = await cur.fetchone()
            return row is not None

    async def increment_error_count(self, file_path: str) -> int:
        """Bump recurrence counter for a file in file_context; returns new count."""
        now = _utc_now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE file_context
                SET error_count = error_count + 1, updated_at = ?
                WHERE file_path = ?
                """,
                (now, file_path),
            )
            await db.commit()
            cur = await db.execute(
                "SELECT error_count FROM file_context WHERE file_path = ?",
                (file_path,),
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 1

    async def snooze_incident(self, incident_id: str, minutes: int) -> bool:
        """Set snoozed_until to now+minutes for the given incident. Returns True if found."""
        until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "UPDATE incident_log SET snoozed_until = ? WHERE incident_id = ?",
                (until, incident_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def is_signature_snoozed(
        self, file_path: str, line_number: int, error_type: str
    ) -> bool:
        """True if any incident for this bug signature has an active snooze."""
        if not error_type:
            return False
        pattern = f"%{error_type}%"
        now = _utc_now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                """
                SELECT 1 FROM incident_log
                WHERE file_path = ? AND line_number = ? AND root_cause LIKE ?
                  AND snoozed_until IS NOT NULL AND snoozed_until > ?
                LIMIT 1
                """,
                (file_path, int(line_number), pattern, now),
            )
            return await cur.fetchone() is not None

    async def get_context_for_file(self, file_path: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM file_context WHERE file_path = ?", (file_path,)
            )
            row = await cur.fetchone()
            if row is None:
                return None
            return _row_to_dict(row)

    async def log_incident(self, incident_dict: dict[str, Any]) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute(
                    """
                    INSERT INTO incident_log (
                        incident_id, trace_id, commit_sha, user_action, log_trace,
                        file_path, line_number, root_cause, severity, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        incident_dict["incident_id"],
                        incident_dict["trace_id"],
                        incident_dict.get("commit_sha"),
                        incident_dict.get("user_action", ""),
                        incident_dict.get("log_trace", ""),
                        incident_dict["file_path"],
                        int(incident_dict["line_number"]),
                        incident_dict.get("root_cause", ""),
                        incident_dict.get("severity", "LOW"),
                        incident_dict.get("created_at", _utc_now_iso()),
                    ),
                )
                await db.commit()
            except sqlite3.IntegrityError:
                return False
            return True

    async def get_recent_incidents(self, limit: int = 20) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT * FROM incident_log
                ORDER BY datetime(created_at) DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cur.fetchall()
            return [_row_to_dict(r) for r in rows]

    async def log_webhook_delivery(
        self,
        *,
        incident_id: str | None,
        trace_id: str | None,
        kind: str,
        success: bool,
        detail: str = "",
    ) -> None:
        """Record a webhook send attempt (e.g. Slack) for audit purposes."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO webhook_log (
                    incident_id, trace_id, kind, success, detail, sent_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (incident_id, trace_id, kind, 1 if success else 0, detail[:500], _utc_now_iso()),
            )
            await db.commit()

    async def get_recent_webhook_log(self, limit: int = 20) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM webhook_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def enqueue_webhook_retry(
        self,
        *,
        incident_id: str | None,
        trace_id: str | None,
        kind: str,
        payload: dict[str, Any],
        max_attempts: int = 5,
    ) -> None:
        """Schedule a failed webhook delivery for retry with exponential backoff."""
        now = _utc_now_iso()
        next_attempt_at = (
            datetime.now(timezone.utc) + timedelta(seconds=_RETRY_BASE_DELAY_SECONDS)
        ).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO webhook_retry_queue (
                    incident_id, trace_id, kind, payload, attempt, max_attempts,
                    status, next_attempt_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, 'pending', ?, ?, ?)
                """,
                (
                    incident_id,
                    trace_id,
                    kind,
                    json.dumps(payload, ensure_ascii=False),
                    max_attempts,
                    next_attempt_at,
                    now,
                    now,
                ),
            )
            await db.commit()

    async def get_due_webhook_retries(self, limit: int = 10) -> list[dict[str, Any]]:
        """Pending retries whose backoff window has elapsed, oldest-due first."""
        now = _utc_now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT * FROM webhook_retry_queue
                WHERE status = 'pending' AND next_attempt_at <= ?
                ORDER BY datetime(next_attempt_at) ASC
                LIMIT ?
                """,
                (now, limit),
            )
            rows = await cur.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                try:
                    d["payload"] = json.loads(d["payload"])
                except json.JSONDecodeError:
                    d["payload"] = {}
                result.append(d)
            return result

    async def record_webhook_retry_result(
        self, retry_id: int, attempt: int, success: bool, *, error: str = ""
    ) -> None:
        """Mark a retry attempt outcome: succeeded, exhausted, or rescheduled with backoff."""
        now = _utc_now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            if success:
                await db.execute(
                    """
                    UPDATE webhook_retry_queue
                    SET status = 'succeeded', attempt = ?, updated_at = ?, last_error = NULL
                    WHERE id = ?
                    """,
                    (attempt + 1, now, retry_id),
                )
                await db.commit()
                return

            cur = await db.execute(
                "SELECT max_attempts FROM webhook_retry_queue WHERE id = ?", (retry_id,)
            )
            row = await cur.fetchone()
            max_attempts = int(row[0]) if row else 5
            new_attempt = attempt + 1

            if new_attempt >= max_attempts:
                await db.execute(
                    """
                    UPDATE webhook_retry_queue
                    SET status = 'exhausted', attempt = ?, updated_at = ?, last_error = ?
                    WHERE id = ?
                    """,
                    (new_attempt, now, error[:500], retry_id),
                )
            else:
                delay = _RETRY_BASE_DELAY_SECONDS * (2**new_attempt)
                next_attempt_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=delay)
                ).isoformat()
                await db.execute(
                    """
                    UPDATE webhook_retry_queue
                    SET attempt = ?, updated_at = ?, last_error = ?, next_attempt_at = ?
                    WHERE id = ?
                    """,
                    (new_attempt, now, error[:500], next_attempt_at, retry_id),
                )
            await db.commit()

    async def get_recent_webhook_retries(self, limit: int = 20) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM webhook_retry_queue ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_all_file_contexts(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM file_context ORDER BY datetime(updated_at) DESC"
            )
            rows = await cur.fetchall()
            return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    d = dict(row)
    for key in ("last_error",):
        if d.get(key) and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except json.JSONDecodeError:
                pass
    if d.get("generated_tests") and isinstance(d["generated_tests"], str):
        try:
            d["generated_tests"] = json.loads(d["generated_tests"])
        except json.JSONDecodeError:
            d["generated_tests"] = []
    return d


def _sqlite_row_to_dict_sync(row: sqlite3.Row) -> dict[str, Any]:
    d = {k: row[k] for k in row.keys()}
    if d.get("last_error") and isinstance(d["last_error"], str):
        try:
            d["last_error"] = json.loads(d["last_error"])
        except json.JSONDecodeError:
            pass
    if d.get("generated_tests") and isinstance(d["generated_tests"], str):
        try:
            d["generated_tests"] = json.loads(d["generated_tests"])
        except json.JSONDecodeError:
            d["generated_tests"] = []
    return d


def read_recent_incidents_sync(db_path: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    path = str(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """
            SELECT * FROM incident_log
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [_sqlite_row_to_dict_sync(r) for r in cur.fetchall()]
    finally:
        conn.close()


def snooze_incident_sync(db_path: str | Path, incident_id: str, minutes: int) -> bool:
    """Set snoozed_until on an incident (synchronous, for Flask routes)."""
    until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "UPDATE incident_log SET snoozed_until = ? WHERE incident_id = ?",
            (until, incident_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def retry_webhook_now_sync(db_path: str | Path, retry_id: int) -> bool:
    """Force an exhausted retry row back to 'pending' with next_attempt_at=now.

    Only exhausted rows are eligible — pending/succeeded rows are left alone so
    this can't be used to jump the backoff queue. max_attempts is bumped by one
    so the forced attempt still goes through record_webhook_retry_result's
    normal max_attempts check: one more real send, and if it fails the row goes
    back to exhausted rather than looping forever.

    The forcing action itself is logged to webhook_log with a ':manual_retry'
    kind suffix (mirroring the existing ':retry' suffix used for automatic
    retries) so the dashboard audit trail shows when/what was manually forced,
    distinct from the actual send outcome that record_webhook_retry_result
    logs once the forced attempt runs on the next pipeline tick.
    """
    now = _utc_now_iso()
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT incident_id, trace_id, kind FROM webhook_retry_queue WHERE id = ? AND status = 'exhausted'",
            (retry_id,),
        ).fetchone()
        if row is None:
            return False
        incident_id, trace_id, kind = row

        cur = conn.execute(
            """
            UPDATE webhook_retry_queue
            SET status = 'pending', next_attempt_at = ?, updated_at = ?,
                max_attempts = max_attempts + 1
            WHERE id = ? AND status = 'exhausted'
            """,
            (now, now, retry_id),
        )
        if cur.rowcount > 0:
            conn.execute(
                """
                INSERT INTO webhook_log (
                    incident_id, trace_id, kind, success, detail, sent_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    incident_id,
                    trace_id,
                    f"{kind}:manual_retry",
                    1,
                    f"retry #{retry_id} manually forced from dashboard",
                    now,
                ),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def count_recent_manual_retries_sync(db_path: str | Path, hours: int = 168) -> int:
    """Count ':manual_retry' webhook_log rows within the last `hours`.

    Counts the forcing action itself (logged by retry_webhook_now_sync), not
    the eventual send outcome, so this reflects how often someone reached for
    the dashboard's "Retry now" button rather than how many of those retries
    ultimately succeeded.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            """
            SELECT COUNT(*) FROM webhook_log
            WHERE kind LIKE '%:manual_retry' AND sent_at >= ?
            """,
            (since,),
        )
        return cur.fetchone()[0]
    finally:
        conn.close()


def read_recent_webhook_log_sync(db_path: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    path = str(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT * FROM webhook_log ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def read_recent_webhook_retries_sync(db_path: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    path = str(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT * FROM webhook_retry_queue ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def read_all_file_contexts_sync(db_path: str | Path) -> list[dict[str, Any]]:
    path = str(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT * FROM file_context ORDER BY datetime(updated_at) DESC"
        )
        return [_sqlite_row_to_dict_sync(r) for r in cur.fetchall()]
    finally:
        conn.close()
