"""Red/green regression suite for audit-remediation package C1 (MED modality_guard
fail-open): `enforce()` silently returns when the resolved capability is absent
(`allowed is None`), letting an unsupported-modality request through with ZERO
observability — a genuinely misconfigured/un-seeded catalog entry is invisible in
production.

DECISION (see final report for full rationale): the fail-open behavior itself is a
DELIBERATE, Tin-approved, FROZEN @ v1 contract (`.add/tasks/unsupported-input-guard/
TASK.md` §1 "FAIL-OPEN ... design-for-failure", §2 "Fail-open on an unknown model
id ... guard ALLOWS the request", §3 CONTRACT). Flipping it to a 400 would violate
the frozen contract, break `test_enforce_allowed_none_passes` /
`test_fail_open_unknown_model` (tests/input_modality_guard/test_input_modality_guard.py,
also FROZEN), and 4xx real traffic to any not-yet-cataloged model — exactly the
outcome the frozen spec explicitly rejected. Kept fail-open; added a WARNING log
line so the previously-silent pass-through is now visible, gated on a genuinely
UNCHECKABLE non-text/video modality (text is near-universal and safe; logging every
plain-text fail-open would be pure noise) to keep production log volume sane.

New file (does not edit the FROZEN test_input_modality_guard.py suite).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Generator

from gateway.proxy.application.modality_guard import enforce

_LOGGER_NAME = "gateway.proxy.application.modality_guard"


@contextlib.contextmanager
def _capture(level: int = logging.WARNING) -> Generator[list[logging.LogRecord]]:
    """Minimal handler-based log capture, scoped to modality_guard's own logger."""
    logger = logging.getLogger(_LOGGER_NAME)
    records: list[logging.LogRecord] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Handler(level=level)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(level)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def test_fail_open_with_non_text_modality_logs_a_warning() -> None:
    """A request needing "image" against an unknown-capability model must still be
    ALLOWED (fail-open, unchanged) but must now emit a WARNING so the previously
    invisible pass-through is observable."""
    with _capture() as records:
        enforce(frozenset({"text", "image"}), None, model_id="ghost-model")

    assert records, "expected a WARNING log line on fail-open with a non-text modality"
    assert any(
        "ghost-model" in r.getMessage() or r.__dict__.get("model_id") == "ghost-model"
        for r in records
    )


def test_fail_open_with_only_text_does_not_log() -> None:
    """Plain-text fail-open is the common/safe case (text is near-universal) — must
    stay silent to avoid flooding production logs on every un-seeded model."""
    with _capture() as records:
        enforce(frozenset({"text"}), None, model_id="ghost-model-2")

    assert not records, "plain-text fail-open must not log at WARNING (noise control)"


def test_fail_open_still_allows_the_request_unchanged() -> None:
    """The core frozen-contract behavior is UNCHANGED: fail-open never raises."""
    # Should not raise — same as the frozen test_enforce_allowed_none_passes.
    enforce(frozenset({"image"}), None, model_id="some-model")
