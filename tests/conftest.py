"""Shared pytest fixtures for ContextO regression tests."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow importing modules from the project root (e.g. billing_logic.py).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
