"""Structlog configuration for the gateway.

Call configure_structlog() once at app startup (idempotent).

test_capture: if a list is passed, log events are appended to that list
              as plain dicts instead of writing JSON to stdout.
              This is the contracted test hook for the observability suite.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger

# Tracks whether configure_structlog has been called in production mode
_configured: bool = False


def _make_test_sink(capture: list[dict[str, Any]]) -> Any:
    """Return a structlog processor that appends the event dict to `capture`."""

    def _sink(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> str:
        # Append a shallow copy so mutations don't affect already-captured entries
        capture.append(dict(event_dict))
        return ""

    return _sink


def configure_structlog(
    *,
    test_capture: list[dict[str, Any]] | None = None,
) -> None:
    """Configure structlog for the gateway.

    Idempotent for the production path (no test_capture).  When test_capture is
    supplied the configuration is always re-applied so successive test calls each
    get their own capture list wired in.

    Safety: Authorization header values and raw key material must NEVER be bound
    to any log context — the middleware enforces this by not reading those headers.
    """
    global _configured  # module-level flag for idempotency guard

    # In production (no test_capture), only configure once
    if test_capture is None and _configured:
        return

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if test_capture is not None:
        final_processor: Any = _make_test_sink(test_capture)
        logger_factory: Any = structlog.PrintLoggerFactory(file=sys.stdout)
    else:
        final_processor = structlog.processors.JSONRenderer()
        logger_factory = structlog.PrintLoggerFactory(file=sys.stdout)

    structlog.configure(
        processors=[*shared_processors, final_processor],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        context_class=dict,
        logger_factory=logger_factory,
        # False so test re-configuration with a new capture list takes effect immediately
        cache_logger_on_first_use=False,
    )

    # Suppress noisy stdlib loggers from third-party libs (SQLAlchemy, httpx, etc.)
    # so they don't appear in test output or pollute production logs.
    logging.getLogger().setLevel(logging.WARNING)

    if test_capture is None:
        _configured = True
