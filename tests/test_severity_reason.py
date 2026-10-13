"""Tests for severity explanations (explain() + incident_log.severity_reason)."""

from __future__ import annotations

import asyncio
import itertools
import sqlite3

from contexto.memory.context_store import ContextStore
from contexto.notifications.slack_notifier import build_payload
from contexto.severity import DEFAULT_POLICY, classify, explain, policy_from_dict

POLICY = policy_from_dict(
    {
        "rules": [
            {"pattern": "tests/*", "max_severity": "LOW"},
            {"pattern": "billing_logic.py", "max_severity": "MEDIUM"},
            {"pattern": "*auth_fixtures*", "severity": "LOW"},
            {"pattern": "report_engine.py", "severity": "MEDIUM", "max_severity": "MEDIUM"},
        ]
    }
)


def test_explain_agrees_with_classify_everywhere():
    paths = [
        "billing_logic.py",
        "/srv/app/billing_logic.py",
        "tests/auth_fixtures.py",
        "src/auth_fixtures.py",
        "report_engine.py",
        "archive_service.py",
        "",
    ]
    for policy, fp, count, et in itertools.product(
        (DEFAULT_POLICY, POLICY), paths, range(8), ("", "KeyError", "ZeroDivisionError")
    ):
        severity, reason = explain(fp, count, et, policy)
        assert severity == classify(fp, count, et, policy)
        assert reason


def test_reason_names_keyword():
    assert explain("billing_logic.py", 0, "") == ("HIGH", "critical path keyword 'billing'")


def test_reason_names_hit_count_threshold():
    assert explain("archive_service.py", 7, "") == ("HIGH", "file seen 7x (>= 5)")
    assert explain("archive_service.py", 3, "") == ("MEDIUM", "file seen 3x (>= 2)")


def test_reason_names_error_type():
    assert explain("archive_service.py", 0, "KeyError") == (
        "MEDIUM",
        "KeyError is a common runtime error",
    )


def test_reason_for_plain_low():
    severity, reason = explain("archive_service.py", 0, "ZeroDivisionError")
    assert severity == "LOW"
    assert "no critical keyword" in reason


def test_reason_names_floor_rule_by_index_and_pattern():
    severity, reason = explain("src/auth_fixtures.py", 0, "", POLICY)
    assert severity == "LOW"
    assert reason == 'rules[2] "*auth_fixtures*" sets floor LOW'


def test_reason_names_ceiling_and_what_it_overrode():
    severity, reason = explain("tests/auth_fixtures.py", 9, "", POLICY)
    assert severity == "LOW"
    # First match wins: tests/* (rules[0]), not *auth_fixtures* (rules[2]).
    assert reason.startswith('rules[0] "tests/*" caps at LOW (would be HIGH: ')

    severity, reason = explain("/srv/app/billing_logic.py", 0, "", POLICY)
    assert severity == "MEDIUM"
    assert reason == (
        "rules[1] \"billing_logic.py\" caps at MEDIUM "
        "(would be HIGH: critical path keyword 'billing')"
    )


def test_ceiling_that_does_not_bind_is_not_credited():
    # rules[3] has a MEDIUM ceiling too, but the result never exceeds it, so only the floor is named.
    severity, reason = explain("report_engine.py", 0, "", POLICY)
    assert (severity, reason) == ("MEDIUM", 'rules[3] "report_engine.py" sets floor MEDIUM')


def test_floor_credited_when_it_ties_hit_count():
    severity, reason = explain("billing_logic.py", 9, "")
    assert (severity, reason) == ("HIGH", "critical path keyword 'billing'")


def _section_text(payload):
    return payload["blocks"][1]["fields"][2]["text"]


def test_slack_payload_shows_reason_under_severity():
    payload = build_payload(
        {
            "trace_id": "abc",
            "file_path": "tests/auth_fixtures.py",
            "line_number": 3,
            "severity": "LOW",
            "severity_reason": 'rules[0] "tests/*" caps at LOW',
        }
    )
    assert _section_text(payload).endswith('\n_rules[0] "tests/*" caps at LOW_')


def test_slack_payload_without_reason_is_unchanged():
    payload = build_payload({"trace_id": "abc", "file_path": "x.py", "severity": "LOW"})
    assert _section_text(payload) == "*Severity*\n:large_green_circle: LOW"


def test_store_migrates_and_round_trips_reason(tmp_path):
    db = tmp_path / "old.db"
    # A pre-feature database: incident_log without the severity_reason column.
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE incident_log (
                incident_id TEXT PRIMARY KEY, trace_id TEXT UNIQUE, commit_sha TEXT,
                user_action TEXT, log_trace TEXT, file_path TEXT, line_number INTEGER,
                root_cause TEXT, severity TEXT DEFAULT 'LOW', created_at TEXT)"""
        )
        conn.execute(
            "INSERT INTO incident_log (incident_id, trace_id, file_path, line_number) "
            "VALUES ('old', 't-old', 'a.py', 1)"
        )

    async def run():
        store = ContextStore(str(db))
        await store.init()
        await store.init()  # migration is idempotent
        stored = await store.log_incident(
            {
                "incident_id": "new",
                "trace_id": "t-new",
                "file_path": "billing_logic.py",
                "line_number": 2,
                "severity": "HIGH",
                "severity_reason": "critical path keyword 'billing'",
                "created_at": "2026-10-13T00:00:00+00:00",
            }
        )
        assert stored
        return await store.get_recent_incidents()

    rows = {r["incident_id"]: r for r in asyncio.run(run())}
    assert rows["new"]["severity_reason"] == "critical path keyword 'billing'"
    assert rows["old"]["severity_reason"] is None
