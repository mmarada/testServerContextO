"""Incident severity classification — HIGH / MEDIUM / LOW."""

from __future__ import annotations

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


def classify(file_path: str, error_count: int, error_type: str) -> str:
    """Return 'HIGH', 'MEDIUM', or 'LOW' for an incident.

    HIGH  — file touches money/auth paths OR seen >=5 times.
    MEDIUM — 2–4 hits OR a common runtime error type.
    LOW   — first occurrence with no critical context.
    """
    fp_lower = (file_path or "").lower()
    is_critical_file = any(kw in fp_lower for kw in _CRITICAL_FILE_KEYWORDS)

    if is_critical_file or error_count >= HIGH_THRESHOLD:
        return "HIGH"

    is_runtime_error = any(
        error_type.startswith(e) for e in _COMMON_RUNTIME_ERRORS
    )

    if error_count >= 2 or is_runtime_error:
        return "MEDIUM"

    return "LOW"
