"""Unit tests for the LOW-severity Slack digest."""
from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from config import Settings
from contexto import pipeline
from contexto.memory.context_store import (
    LOW_DIGEST_KIND,
    ContextStore,
    count_pending_digest_sync,
)
from contexto.notifications.slack_notifier import build_digest_payload
from contexto.pipeline import _low_digest_due, _process_low_digest

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def _due(**overrides):
    kwargs = dict(
        oldest_pending_at=NOW - timedelta(hours=1),
        last_success_at=None,
        last_attempt_at=None,
        consecutive_failures=0,
        interval_hours=24,
        retry_base_delay_seconds=30,
    )
    kwargs.update(overrides)
    return _low_digest_due(NOW, **kwargs)


def _settings(**overrides):
    return Settings(
        GITHUB_PERSONAL_ACCESS_TOKEN="x",
        GITHUB_OWNER="o",
        GITHUB_REPO="r",
        GOOGLE_API_KEY="k",
        SLACK_WEBHOOK_URL="https://hooks.example/test",
        **overrides,
    )


def test_digest_disabled_by_default():
    assert _settings().low_digest_interval_hours == 0


def test_not_due_with_nothing_pending():
    assert not _due(oldest_pending_at=None)


def test_waits_full_window_from_oldest_pending():
    assert not _due(oldest_pending_at=NOW - timedelta(hours=23))
    assert _due(oldest_pending_at=NOW - timedelta(hours=24))


def test_stale_last_success_does_not_fire_early():
    """A digest sent a week ago must not make a brand-new LOW incident send instantly."""
    assert not _due(
        oldest_pending_at=NOW - timedelta(minutes=5),
        last_success_at=NOW - timedelta(days=7),
    )


def test_recent_success_pushes_baseline_forward():
    assert not _due(
        oldest_pending_at=NOW - timedelta(hours=30),
        last_success_at=NOW - timedelta(hours=2),
    )


def test_failure_backoff_is_exponential_and_capped():
    # 3 failures -> 30 * 2^2 = 120s
    assert not _due(
        consecutive_failures=3, last_attempt_at=NOW - timedelta(seconds=119)
    )
    assert _due(consecutive_failures=3, last_attempt_at=NOW - timedelta(seconds=120))
    # 20 failures would be ~182 days of backoff; capped at the 1h window
    assert _due(
        interval_hours=1,
        consecutive_failures=20,
        last_attempt_at=NOW - timedelta(hours=1),
    )


def test_disabled_interval_flushes_leftovers():
    assert _due(interval_hours=0, oldest_pending_at=NOW)


def test_digest_payload_groups_by_file_and_caps_lines():
    incidents = [
        {"file_path": "report_engine.py", "line_number": 10, "root_cause": "OSError: a"},
        {"file_path": "report_engine.py", "line_number": 12, "root_cause": "OSError: b"},
    ] + [
        {"file_path": f"mod_{i}.py", "line_number": i, "root_cause": "Warn"}
        for i in range(20)
    ]
    payload = build_digest_payload(incidents, 24)
    text = str(payload)
    assert "22 low-severity incidents" in text
    assert "across 21 files" in text
    assert "`report_engine.py:12` ×2" in text  # grouped, latest line shown
    assert "and 6 more files" in text
    body = payload["blocks"][2]["text"]["text"]
    assert body.startswith("• `report_engine.py")  # noisiest file first
    assert len(body) <= 2900


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = ContextStore(str(Path(tmp) / "test.db"))
        asyncio.run(s.init())
        yield s


def _log(store, incident_id):
    asyncio.run(
        store.log_incident(
            {
                "incident_id": incident_id,
                "trace_id": f"t-{incident_id}",
                "file_path": "report_engine.py",
                "line_number": 5,
                "root_cause": "OSError: disk",
                "severity": "LOW",
            }
        )
    )
    asyncio.run(store.queue_incident_for_digest(incident_id))


def test_process_digest_sends_once_and_marks_delivered(store):
    _log(store, "a")
    _log(store, "b")
    assert count_pending_digest_sync(store.db_path) == 2

    settings = _settings(LOW_DIGEST_INTERVAL_HOURS=0)  # flush immediately
    post = mock.AsyncMock(return_value=True)
    with mock.patch.object(pipeline, "post_slack_payload", post):
        asyncio.run(_process_low_digest(store, settings))
        asyncio.run(_process_low_digest(store, settings))

    assert post.await_count == 1
    assert count_pending_digest_sync(store.db_path) == 0
    state = asyncio.run(store.get_digest_delivery_state())
    assert state["last_success_at"] is not None
    assert state["consecutive_failures"] == 0


def test_failed_digest_keeps_incidents_and_backs_off(store):
    _log(store, "a")
    settings = _settings(LOW_DIGEST_INTERVAL_HOURS=0)
    post = mock.AsyncMock(return_value=False)
    with mock.patch.object(pipeline, "post_slack_payload", post):
        asyncio.run(_process_low_digest(store, settings))
        # immediate second tick is inside the 30s backoff window
        asyncio.run(_process_low_digest(store, settings))

    assert post.await_count == 1
    assert count_pending_digest_sync(store.db_path) == 1
    state = asyncio.run(store.get_digest_delivery_state())
    assert state["consecutive_failures"] == 1
    assert state["last_success_at"] is None

    log = asyncio.run(store.get_recent_webhook_log())
    assert log[0]["kind"] == LOW_DIGEST_KIND and log[0]["success"] == 0
