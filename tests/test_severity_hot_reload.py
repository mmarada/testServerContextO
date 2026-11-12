"""Hot-reload of SEVERITY_RULES_PATH (PolicyReloader)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from contexto.severity import DEFAULT_POLICY, PolicyReloader, classify

CAP_ARCHIVE = 'rules:\n  - pattern: "archive_service.py"\n    max_severity: LOW\n'
FLOOR_REPORT = 'rules:\n  - pattern: "report_engine.py"\n    severity: HIGH\n'


class _Log(list):
    def __call__(self, msg: str) -> None:
        self.append(msg)


def _write(path: Path, text: str, mtime_ns: int) -> None:
    """Write and pin mtime so tests never depend on filesystem timestamp granularity."""
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(mtime_ns, mtime_ns))


@pytest.fixture
def rules(tmp_path: Path) -> Path:
    p = tmp_path / "rules.yaml"
    _write(p, CAP_ARCHIVE, 1_000_000_000_000)
    return p


def test_no_path_serves_default_without_touching_disk():
    r = PolicyReloader("", log=_Log())
    assert r.current() is DEFAULT_POLICY
    assert r.reloads == 0


def test_bad_file_at_startup_still_fails_fast(tmp_path: Path):
    p = tmp_path / "rules.yaml"
    p.write_text("rules: [{pattern: x, severity: CRITICAL}]", encoding="utf-8")
    with pytest.raises(ValueError, match="rules\\[0\\]"):
        PolicyReloader(str(p))
    with pytest.raises(ValueError, match="missing file"):
        PolicyReloader(str(tmp_path / "nope.yaml"))


def test_unchanged_file_is_not_reparsed(rules: Path, monkeypatch):
    r = PolicyReloader(str(rules), log=_Log())
    first = r.current()
    calls = []
    monkeypatch.setattr("contexto.severity.load_policy", lambda p: calls.append(p))
    for _ in range(5):
        assert r.current() is first
    assert calls == []


def test_edit_is_picked_up_on_next_call(rules: Path):
    log = _Log()
    r = PolicyReloader(str(rules), log=log)
    assert classify("archive_service.py", 40, "KeyError", r.current()) == "LOW"
    assert classify("report_engine.py", 0, "", r.current()) == "LOW"

    _write(rules, FLOOR_REPORT, 2_000_000_000_000)
    policy = r.current()
    assert classify("archive_service.py", 40, "KeyError", policy) == "HIGH"
    assert classify("report_engine.py", 0, "", policy) == "HIGH"
    assert r.reloads == 1
    assert r.last_error is None
    assert any("reloaded" in m and "1 -> 1 rules" in m for m in log)


def test_same_mtime_different_size_is_detected(rules: Path):
    r = PolicyReloader(str(rules), log=_Log())
    two_rules = CAP_ARCHIVE + '  - pattern: "tests/*"\n    max_severity: LOW\n'
    _write(rules, two_rules, 1_000_000_000_000)  # identical mtime
    assert len(r.current().rules) == 2


def test_atomic_rename_save_is_detected(rules: Path, tmp_path: Path):
    """Editors that write a temp file and rename it change the inode, not always size/mtime."""
    r = PolicyReloader(str(rules), log=_Log())
    swapped = CAP_ARCHIVE.replace("archive", "achive_")  # same byte length
    assert len(swapped) == len(CAP_ARCHIVE)
    tmp = tmp_path / "rules.yaml.tmp"
    _write(tmp, swapped, 1_000_000_000_000)  # same mtime too
    os.replace(tmp, rules)
    assert r.current().rules[0].pattern == "achive__service.py"


def test_bad_edit_keeps_last_good_policy_and_logs_once(rules: Path):
    log = _Log()
    r = PolicyReloader(str(rules), log=log)
    good = r.current()

    _write(rules, "rules:\n  - pattern: x\n    severity: SEV1\n", 2_000_000_000_000)
    for _ in range(4):
        assert r.current() is good
    rejected = [m for m in log if "rejected" in m]
    assert len(rejected) == 1
    assert "rules[0]" in rejected[0] and "keeping last good policy (1 rules)" in rejected[0]
    assert "SEV1" in r.last_error

    # Still the capped policy — NOT a silent fallback to DEFAULT_POLICY.
    assert classify("archive_service.py", 40, "KeyError", r.current()) == "LOW"

    # A second, different bad revision is reported again.
    _write(rules, "rules: {", 3_000_000_000_000)  # YAML syntax error, not ValueError
    assert r.current() is good
    assert len([m for m in log if "rejected" in m]) == 2

    # Fixing it recovers on the next tick and clears the error.
    _write(rules, FLOOR_REPORT, 4_000_000_000_000)
    assert classify("report_engine.py", 0, "", r.current()) == "HIGH"
    assert r.last_error is None
    assert r.reloads == 1


def test_deleted_file_keeps_last_good_then_recovers(rules: Path):
    log = _Log()
    r = PolicyReloader(str(rules), log=log)
    good = r.current()
    rules.unlink()
    assert r.current() is good
    assert r.current() is good
    assert len([m for m in log if "missing" in m]) == 1
    assert "missing" in r.last_error

    _write(rules, FLOOR_REPORT, 5_000_000_000_000)
    assert classify("report_engine.py", 0, "", r.current()) == "HIGH"
    assert r.last_error is None


def test_touch_without_change_does_not_count_as_reload(rules: Path):
    log = _Log()
    r = PolicyReloader(str(rules), log=log)
    good = r.current()
    _write(rules, CAP_ARCHIVE, 9_000_000_000_000)
    assert r.current() is good
    assert r.reloads == 0
    assert any("rules unchanged" in m for m in log)


def test_thresholds_reload_too(rules: Path):
    r = PolicyReloader(str(rules), log=_Log())
    assert classify("report_engine.py", 3, "", r.current()) == "MEDIUM"
    _write(rules, "high_count_threshold: 3\nmedium_count_threshold: 2\n", 2_000_000_000_000)
    assert classify("report_engine.py", 3, "", r.current()) == "HIGH"
