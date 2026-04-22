"""Async SQLite persistence for file context and incidents."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
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
    created_at TEXT
);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ContextStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)
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

    async def increment_error_count(self, file_path: str) -> None:
        """Bump recurrence counter for a file in file_context."""
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
                        file_path, line_number, root_cause, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
