"""Unit tests for the webhook retry queue in ContextStore."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from contexto.memory.context_store import ContextStore, read_recent_webhook_retries_sync


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        s = ContextStore(db_path)
        asyncio.run(s.init())
        yield s


def test_enqueue_creates_pending_row(store):
    asyncio.run(
        store.enqueue_webhook_retry(
            incident_id="inc-1",
            trace_id="trace-1",
            kind="slack:new_incident",
            payload={"trace_id": "trace-1", "root_cause": "KeyError"},
        )
    )
    rows = read_recent_webhook_retries_sync(store.db_path)
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert rows[0]["attempt"] == 0
    assert rows[0]["max_attempts"] == 5


def test_not_due_immediately_after_enqueue(store):
    """Backoff means a freshly-queued retry shouldn't fire before its window elapses."""
    asyncio.run(
        store.enqueue_webhook_retry(
            incident_id="inc-1",
            trace_id="trace-1",
            kind="slack:new_incident",
            payload={"trace_id": "trace-1"},
        )
    )
    due = asyncio.run(store.get_due_webhook_retries())
    assert due == []


def test_success_marks_succeeded(store):
    asyncio.run(
        store.enqueue_webhook_retry(
            incident_id="inc-1",
            trace_id="trace-1",
            kind="slack:new_incident",
            payload={"trace_id": "trace-1"},
        )
    )
    rows = read_recent_webhook_retries_sync(store.db_path)
    retry_id = rows[0]["id"]

    asyncio.run(store.record_webhook_retry_result(retry_id, 0, True))

    rows = read_recent_webhook_retries_sync(store.db_path)
    assert rows[0]["status"] == "succeeded"
    due = asyncio.run(store.get_due_webhook_retries())
    assert due == []


def test_failure_reschedules_with_backoff_until_exhausted(store):
    asyncio.run(
        store.enqueue_webhook_retry(
            incident_id="inc-1",
            trace_id="trace-1",
            kind="slack:new_incident",
            payload={"trace_id": "trace-1"},
            max_attempts=2,
        )
    )
    rows = read_recent_webhook_retries_sync(store.db_path)
    retry_id = rows[0]["id"]

    asyncio.run(store.record_webhook_retry_result(retry_id, 0, False, error="boom"))
    rows = read_recent_webhook_retries_sync(store.db_path)
    assert rows[0]["status"] == "pending"
    assert rows[0]["attempt"] == 1
    assert rows[0]["last_error"] == "boom"

    asyncio.run(store.record_webhook_retry_result(retry_id, 1, False, error="boom again"))
    rows = read_recent_webhook_retries_sync(store.db_path)
    assert rows[0]["status"] == "exhausted"
    assert rows[0]["attempt"] == 2


def test_payload_round_trips_through_due_query(store):
    payload = {"trace_id": "trace-1", "root_cause": "KeyError: 'x'", "severity": "HIGH"}
    asyncio.run(
        store.enqueue_webhook_retry(
            incident_id="inc-1",
            trace_id="trace-1",
            kind="slack:new_incident",
            payload=payload,
        )
    )
    rows = read_recent_webhook_retries_sync(store.db_path)
    retry_id = rows[0]["id"]

    # Force it due by resetting next_attempt_at into the past, mirroring what
    # the pipeline's backoff timer would eventually produce.
    import sqlite3

    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "UPDATE webhook_retry_queue SET next_attempt_at = '2000-01-01T00:00:00+00:00' "
        "WHERE id = ?",
        (retry_id,),
    )
    conn.commit()
    conn.close()

    due = asyncio.run(store.get_due_webhook_retries())
    assert len(due) == 1
    assert due[0]["payload"] == payload
