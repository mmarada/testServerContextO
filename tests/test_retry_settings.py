"""Unit tests for the configurable webhook-retry backoff/max-attempts settings."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from config import Settings
from contexto.memory.context_store import ContextStore, read_recent_webhook_retries_sync


def test_default_retry_settings():
    s = Settings(
        GITHUB_PERSONAL_ACCESS_TOKEN="x",
        GITHUB_OWNER="o",
        GITHUB_REPO="r",
        GOOGLE_API_KEY="k",
    )
    assert s.retry_base_delay_seconds == 30
    assert s.retry_max_attempts == 5


def test_retry_settings_read_from_env():
    s = Settings(
        GITHUB_PERSONAL_ACCESS_TOKEN="x",
        GITHUB_OWNER="o",
        GITHUB_REPO="r",
        GOOGLE_API_KEY="k",
        RETRY_BASE_DELAY_SECONDS=5,
        RETRY_MAX_ATTEMPTS=2,
    )
    assert s.retry_base_delay_seconds == 5
    assert s.retry_max_attempts == 2


@pytest.fixture()
def custom_store():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        s = ContextStore(db_path, retry_base_delay_seconds=1, retry_max_attempts=2)
        asyncio.run(s.init())
        yield s


def test_enqueue_uses_store_max_attempts_when_not_overridden(custom_store):
    """max_attempts defaults to the store's configured value, not the old hardcoded 5."""
    asyncio.run(
        custom_store.enqueue_webhook_retry(
            incident_id="inc-1",
            trace_id="trace-1",
            kind="slack:new_incident",
            payload={"trace_id": "trace-1"},
        )
    )
    rows = read_recent_webhook_retries_sync(custom_store.db_path)
    assert rows[0]["max_attempts"] == 2


def test_enqueue_still_accepts_explicit_max_attempts_override(custom_store):
    asyncio.run(
        custom_store.enqueue_webhook_retry(
            incident_id="inc-1",
            trace_id="trace-1",
            kind="slack:new_incident",
            payload={"trace_id": "trace-1"},
            max_attempts=9,
        )
    )
    rows = read_recent_webhook_retries_sync(custom_store.db_path)
    assert rows[0]["max_attempts"] == 9


def test_shorter_base_delay_becomes_immediately_due(custom_store):
    """A short configured base delay means the retry is due almost right away."""
    import time

    asyncio.run(
        custom_store.enqueue_webhook_retry(
            incident_id="inc-1",
            trace_id="trace-1",
            kind="slack:new_incident",
            payload={"trace_id": "trace-1"},
        )
    )
    time.sleep(1.1)
    due = asyncio.run(custom_store.get_due_webhook_retries())
    assert len(due) == 1


def test_exhaustion_respects_store_max_attempts(custom_store):
    asyncio.run(
        custom_store.enqueue_webhook_retry(
            incident_id="inc-1",
            trace_id="trace-1",
            kind="slack:new_incident",
            payload={"trace_id": "trace-1"},
        )
    )
    rows = read_recent_webhook_retries_sync(custom_store.db_path)
    retry_id = rows[0]["id"]

    asyncio.run(custom_store.record_webhook_retry_result(retry_id, 0, False, error="boom"))
    rows = read_recent_webhook_retries_sync(custom_store.db_path)
    assert rows[0]["status"] == "pending"
    assert rows[0]["attempt"] == 1

    asyncio.run(custom_store.record_webhook_retry_result(retry_id, 1, False, error="boom again"))
    rows = read_recent_webhook_retries_sync(custom_store.db_path)
    assert rows[0]["status"] == "exhausted"
    assert rows[0]["attempt"] == 2
