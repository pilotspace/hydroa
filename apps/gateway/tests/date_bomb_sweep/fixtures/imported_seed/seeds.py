"""The seed constant lives HERE, not in the module that pairs it with a relative window.

This is the margin_dashboard shape: `tests/margin_dashboard/conftest.py` defines `INSIDE`
and the test modules import it. A detector that only reads module-local assignments sees
nothing wrong in the sibling and misses the one real instance this repo has ever had.
"""

from __future__ import annotations

import datetime

INSIDE = datetime.datetime(2026, 7, 15, 12, 0, tzinfo=datetime.UTC)
WINDOW_FROM = datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
WINDOW_TO = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC)
