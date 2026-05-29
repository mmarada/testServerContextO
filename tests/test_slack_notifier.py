"""Unit tests for slack_notifier.build_payload."""
import pytest
from contexto.notifications.slack_notifier import build_payload


INCIDENT = {
    "trace_id": "abc12345-def6",
    "file_path": "billing_logic.py",
    "line_number": 42,
    "root_cause": "KeyError: 'tax_rate'",
    "user_action": "INVOICE_PROCESS_CLICK",
}


def test_build_payload_has_blocks():
    payload = build_payload(INCIDENT)
    assert "blocks" in payload
    assert len(payload["blocks"]) >= 3


def test_build_payload_contains_trace_id():
    payload = build_payload(INCIDENT)
    text = str(payload)
    assert "abc12345" in text


def test_build_payload_contains_file_ref():
    payload = build_payload(INCIDENT)
    text = str(payload)
    assert "billing_logic.py" in text
    assert "42" in text


def test_build_payload_truncates_long_root_cause():
    long_cause = "ValueError: " + "x" * 500
    incident = {**INCIDENT, "root_cause": long_cause}
    payload = build_payload(incident)
    text = str(payload)
    # root cause block should be present but truncated
    assert "ValueError" in text
    # Ensure no single string field exceeds 400 chars
    for block in payload["blocks"]:
        for field_key in ("text", "fields"):
            val = block.get(field_key)
            if isinstance(val, str):
                assert len(val) <= 400
            elif isinstance(val, dict):
                assert len(val.get("text", "")) <= 400


def test_build_payload_no_user_action():
    incident = {**INCIDENT, "user_action": ""}
    payload = build_payload(incident)
    # Should still produce valid blocks
    assert "blocks" in payload
