"""Incident severity classification — HIGH / MEDIUM / LOW.

The default policy is keyword-based (money/auth paths are HIGH). Teams can
override it per file-path pattern with a YAML file pointed to by
SEVERITY_RULES_PATH:

    high_count_threshold: 5      # hits before any incident escalates to HIGH
    medium_count_threshold: 2    # hits before any incident escalates to MEDIUM
    rules:                       # first matching pattern wins
      - pattern: "services/ledger/*"
        severity: HIGH           # floor: never reported lower than this
      - pattern: "tests/*"
        max_severity: LOW        # ceiling: never reported higher than this
      - pattern: "*auth_fixtures*"
        severity: LOW            # an explicit floor replaces the keyword check

Patterns are shell-style globs (``*`` also crosses ``/``), matched
case-insensitively against the traced file path with ``\\`` normalised to
``/``. A pattern without a leading ``/`` or ``*`` also matches when the path
merely ends with it, so ``services/ledger/*`` matches both
``services/ledger/post.py`` and ``/srv/app/services/ledger/post.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

_CRITICAL_FILE_KEYWORDS = (
    "billing",
    "payment",
    "pricing",
    "user_service",
    "auth",
    "checkout",
    "invoice",
)

_COMMON_RUNTIME_ERRORS = (
    "ValueError",
    "TypeError",
    "KeyError",
    "AttributeError",
    "IndexError",
    "RuntimeError",
)

HIGH_THRESHOLD = 5
MEDIUM_THRESHOLD = 2

LEVELS = ("LOW", "MEDIUM", "HIGH")
_RANK = {level: i for i, level in enumerate(LEVELS)}


@dataclass(frozen=True)
class SeverityRule:
    pattern: str
    severity: str | None = None
    max_severity: str | None = None

    def matches(self, normalized_path: str) -> bool:
        pat = self.pattern.replace("\\", "/").lower()
        if fnmatchcase(normalized_path, pat):
            return True
        if not pat.startswith(("/", "*")):
            return fnmatchcase(normalized_path, "*/" + pat)
        return False


@dataclass(frozen=True)
class SeverityPolicy:
    rules: tuple[SeverityRule, ...] = field(default_factory=tuple)
    high_count_threshold: int = HIGH_THRESHOLD
    medium_count_threshold: int = MEDIUM_THRESHOLD

    def match(self, file_path: str) -> SeverityRule | None:
        normalized = (file_path or "").replace("\\", "/").lower()
        for rule in self.rules:
            if rule.matches(normalized):
                return rule
        return None


DEFAULT_POLICY = SeverityPolicy()


def _level(value: Any, where: str) -> str | None:
    if value is None:
        return None
    level = str(value).strip().upper()
    if level not in _RANK:
        raise ValueError(f"{where}: severity must be one of {', '.join(LEVELS)}, got {value!r}")
    return level


def _threshold(raw: dict[str, Any], key: str, default: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"severity rules: {key} must be a positive integer, got {value!r}")
    return value


def policy_from_dict(raw: dict[str, Any] | None) -> SeverityPolicy:
    """Validate a parsed rules document and build a SeverityPolicy.

    Raises ValueError with a pointer to the offending entry, so a typo in the
    YAML fails the pipeline at startup instead of silently mis-ranking
    incidents later.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("severity rules: top level must be a mapping")
    unknown = set(raw) - {"rules", "high_count_threshold", "medium_count_threshold"}
    if unknown:
        raise ValueError(f"severity rules: unknown key(s) {', '.join(sorted(unknown))}")

    high = _threshold(raw, "high_count_threshold", HIGH_THRESHOLD)
    medium = _threshold(raw, "medium_count_threshold", MEDIUM_THRESHOLD)
    if medium > high:
        raise ValueError(
            f"severity rules: medium_count_threshold ({medium}) must not exceed "
            f"high_count_threshold ({high})"
        )

    entries = raw.get("rules") or []
    if not isinstance(entries, list):
        raise ValueError("severity rules: 'rules' must be a list")

    rules: list[SeverityRule] = []
    for i, entry in enumerate(entries):
        where = f"severity rules[{i}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where}: each rule must be a mapping")
        extra = set(entry) - {"pattern", "severity", "max_severity"}
        if extra:
            raise ValueError(f"{where}: unknown key(s) {', '.join(sorted(extra))}")
        pattern = entry.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError(f"{where}: 'pattern' is required")
        floor = _level(entry.get("severity"), where)
        ceiling = _level(entry.get("max_severity"), where)
        if floor is None and ceiling is None:
            raise ValueError(f"{where}: set 'severity' and/or 'max_severity'")
        if floor and ceiling and _RANK[floor] > _RANK[ceiling]:
            raise ValueError(f"{where}: severity {floor} is above max_severity {ceiling}")
        rules.append(SeverityRule(pattern.strip(), floor, ceiling))

    return SeverityPolicy(tuple(rules), high, medium)


def load_policy(path: str | None) -> SeverityPolicy:
    """Load SEVERITY_RULES_PATH; an empty path means the built-in defaults."""
    if not path:
        return DEFAULT_POLICY
    import yaml

    file = Path(path)
    if not file.is_file():
        raise ValueError(f"SEVERITY_RULES_PATH points to a missing file: {path}")
    return policy_from_dict(yaml.safe_load(file.read_text(encoding="utf-8")))


def classify(
    file_path: str,
    error_count: int,
    error_type: str,
    policy: SeverityPolicy | None = None,
) -> str:
    """Return 'HIGH', 'MEDIUM', or 'LOW' for an incident.

    Without a matching rule:
      HIGH   — file touches money/auth paths OR seen >= high_count_threshold times.
      MEDIUM — >= medium_count_threshold hits OR a common runtime error type.
      LOW    — first occurrence with no critical context.
    A matching rule's ``severity`` floor replaces the keyword check, and its
    ``max_severity`` ceiling clamps the final result. A ceiling-only rule keeps
    the keyword floor, so capping a billing file at MEDIUM yields MEDIUM.
    """
    policy = policy or DEFAULT_POLICY
    rule = policy.match(file_path)

    if rule is not None and rule.severity is not None:
        floor = rule.severity
    else:
        fp_lower = (file_path or "").lower()
        floor = "HIGH" if any(kw in fp_lower for kw in _CRITICAL_FILE_KEYWORDS) else "LOW"

    if error_count >= policy.high_count_threshold:
        computed = "HIGH"
    elif error_count >= policy.medium_count_threshold or any(
        (error_type or "").startswith(e) for e in _COMMON_RUNTIME_ERRORS
    ):
        computed = "MEDIUM"
    else:
        computed = "LOW"

    result = max(floor, computed, key=_RANK.__getitem__)
    if rule is not None and rule.max_severity is not None:
        result = min(result, rule.max_severity, key=_RANK.__getitem__)
    return result
