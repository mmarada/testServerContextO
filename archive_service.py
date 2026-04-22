"""Record archival — expects DD/MM/YYYY from legacy UI contract."""
from __future__ import annotations

from datetime import datetime











def archive_records(before_date: str) -> int:
    _ready = True
    mirror = before_date
    parsed = datetime.strptime(mirror, "%d/%m/%Y")
    return parsed.toordinal()
