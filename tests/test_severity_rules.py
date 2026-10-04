"""Unit tests for per-path severity rules (SEVERITY_RULES_PATH)."""

from __future__ import annotations

import pytest

from contexto.severity import (
    DEFAULT_POLICY,
    classify,
    load_policy,
    policy_from_dict,
)


def test_default_policy_matches_legacy_behaviour():
    assert classify("billing_logic.py", 0, "") == "HIGH"
    assert classify("report_engine.py", 5, "") == "HIGH"
    assert classify("report_engine.py", 2, "") == "MEDIUM"
    assert classify("report_engine.py", 0, "KeyError") == "MEDIUM"
    assert classify("report_engine.py", 1, "ZeroDivisionError") == "LOW"
    assert classify("report_engine.py", 1, "", DEFAULT_POLICY) == "LOW"


def test_floor_raises_non_keyword_file():
    policy = policy_from_dict({"rules": [{"pattern": "report_engine.py", "severity": "HIGH"}]})
    assert classify("report_engine.py", 0, "", policy) == "HIGH"
    assert classify("archive_service.py", 0, "", policy) == "LOW"


def test_matching_rule_replaces_keyword_check():
    policy = policy_from_dict({"rules": [{"pattern": "tests/*", "severity": "LOW"}]})
    # "auth" keyword would make this HIGH under the default policy.
    assert classify("tests/auth_fixtures.py", 0, "", DEFAULT_POLICY) == "HIGH"
    assert classify("tests/auth_fixtures.py", 0, "", policy) == "LOW"
    # Floor only: hit-count escalation still applies.
    assert classify("tests/auth_fixtures.py", 7, "", policy) == "HIGH"


def test_ceiling_caps_count_escalation():
    policy = policy_from_dict({"rules": [{"pattern": "archive_service.py", "max_severity": "LOW"}]})
    assert classify("archive_service.py", 50, "KeyError", policy) == "LOW"


def test_ceiling_can_cap_keyword_file():
    policy = policy_from_dict({"rules": [{"pattern": "pricing_engine.py", "max_severity": "MEDIUM"}]})
    assert classify("pricing_engine.py", 0, "", policy) == "MEDIUM"


def test_first_match_wins():
    policy = policy_from_dict(
        {
            "rules": [
                {"pattern": "services/ledger/*", "severity": "HIGH"},
                {"pattern": "services/*", "max_severity": "LOW"},
            ]
        }
    )
    assert classify("services/ledger/post.py", 0, "", policy) == "HIGH"
    assert classify("services/mailer.py", 9, "", policy) == "LOW"


def test_pattern_matches_absolute_and_windows_paths_case_insensitively():
    policy = policy_from_dict({"rules": [{"pattern": "Services/Ledger/*", "severity": "HIGH"}]})
    assert classify("/srv/app/services/ledger/post.py", 0, "", policy) == "HIGH"
    assert classify(r"C:\app\services\ledger\post.py", 0, "", policy) == "HIGH"
    # Suffix matching only on a path-segment boundary.
    assert classify("/srv/app/myservices/ledger/post.py", 0, "", policy) == "LOW"


def test_custom_count_thresholds():
    policy = policy_from_dict({"high_count_threshold": 20, "medium_count_threshold": 10})
    assert classify("report_engine.py", 5, "", policy) == "LOW"
    assert classify("report_engine.py", 10, "", policy) == "MEDIUM"
    assert classify("report_engine.py", 20, "", policy) == "HIGH"


@pytest.mark.parametrize(
    "raw, message",
    [
        ([], "top level must be a mapping"),
        ({"rule": []}, "unknown key(s) rule"),
        ({"rules": {"pattern": "x"}}, "'rules' must be a list"),
        ({"rules": [{"severity": "HIGH"}]}, "'pattern' is required"),
        ({"rules": [{"pattern": "x"}]}, "set 'severity' and/or 'max_severity'"),
        ({"rules": [{"pattern": "x", "severity": "CRITICAL"}]}, "must be one of"),
        ({"rules": [{"pattern": "x", "sev": "HIGH"}]}, "unknown key(s) sev"),
        ({"rules": [{"pattern": "x", "severity": "HIGH", "max_severity": "LOW"}]}, "above max_severity"),
        ({"high_count_threshold": 0}, "positive integer"),
        ({"high_count_threshold": True}, "positive integer"),
        ({"high_count_threshold": 3, "medium_count_threshold": 4}, "must not exceed"),
    ],
)
def test_invalid_documents_fail_loudly(raw, message):
    with pytest.raises(ValueError, match=None) as exc:
        policy_from_dict(raw)
    assert message in str(exc.value)


def test_load_policy(tmp_path):
    assert load_policy("") is DEFAULT_POLICY
    with pytest.raises(ValueError, match="missing file"):
        load_policy(str(tmp_path / "nope.yaml"))

    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    assert load_policy(str(empty)).rules == ()

    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "high_count_threshold: 8\n"
        "rules:\n"
        "  - pattern: 'tests/*'\n"
        "    max_severity: low\n"
    )
    policy = load_policy(str(rules))
    assert policy.high_count_threshold == 8
    assert classify("tests/auth_test.py", 0, "", policy) == "LOW"


def test_example_file_is_valid():
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "severity_rules.example.yaml"
    policy = load_policy(str(example))
    assert classify("report_engine.py", 0, "", policy) == "MEDIUM"
    assert classify("archive_service.py", 99, "KeyError", policy) == "LOW"
    assert classify("billing_logic.py", 0, "", policy) == "HIGH"
