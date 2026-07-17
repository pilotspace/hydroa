"""Fire-and-forget email dispatch wrapper — fail-open.

Contract (transactional-email TASK.md §3 — FROZEN @ v1):
  send_email(sender, message) wraps sender.send() exactly like
  gateway.audit.application.audit_writer.record_audit wraps AuditRepository.record:
  catches ALL exceptions, logs + swallows, NEVER raises into the caller. Scheduled via
  asyncio.ensure_future, never awaited on the request hot path.
"""

from __future__ import annotations

import logging

from gateway.email.domain.entities import EmailMessage
from gateway.email.domain.ports import EmailSender

_log = logging.getLogger(__name__)


async def send_email(sender: EmailSender, message: EmailMessage) -> None:
    """Send `message` via `sender` — fail-open.

    Swallows ALL exceptions raised by the adapter — failures are logged but NEVER
    raised. Schedule via asyncio.ensure_future(), never await directly on a request path.
    """
    try:
        await sender.send(message)
    except Exception as exc:
        _log.warning(
            "email_dispatch: failed to send email (swallowed — fail-open)",
            exc_info=exc,
            extra={"email_to": message.to, "email_subject": message.subject},
        )
