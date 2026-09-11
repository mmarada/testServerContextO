"""Unit tests for the configurable recurrence-alert interval."""
from __future__ import annotations

import os
from unittest import mock

import pytest

from config import Settings
from contexto.pipeline import _recurrence_alert_due


def test_default_interval_is_ten():
    with mock.patch.dict(os.environ, {}, clear=False):
        for key in ("RECURRENCE_ALERT_INTERVAL",):
            os.environ.pop(key, None)
        s = Settings(
            GITHUB_PERSONAL_ACCESS_TOKEN="x",
            GITHUB_OWNER="o",
            GITHUB_REPO="r",
            GOOGLE_API_KEY="k",
        )
    assert s.recurrence_alert_interval == 10


def test_interval_read_from_env():
    s = Settings(
        GITHUB_PERSONAL_ACCESS_TOKEN="x",
        GITHUB_OWNER="o",
        GITHUB_REPO="r",
        GOOGLE_API_KEY="k",
        RECURRENCE_ALERT_INTERVAL=3,
    )
    assert s.recurrence_alert_interval == 3


@pytest.mark.parametrize(
    "new_count,interval,expected",
    [
        (10, 10, True),
        (20, 10, True),
        (9, 10, False),
        (11, 10, False),
        (3, 3, True),
        (6, 3, True),
        (4, 3, False),
        (1, 1, True),
        (7, 1, True),
    ],
)
def test_alert_due_on_milestones(new_count, interval, expected):
    assert _recurrence_alert_due(new_count, interval) is expected


@pytest.mark.parametrize("interval", [0, -1, -10])
def test_non_positive_interval_disables_alerts(interval):
    """interval <= 0 disables recurrence alerts instead of raising ZeroDivisionError."""
    for n in (1, 5, 10, 100):
        assert _recurrence_alert_due(n, interval) is False
