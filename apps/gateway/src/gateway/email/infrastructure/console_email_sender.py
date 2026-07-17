"""ConsoleEmailSender — the default EmailSender adapter (transactional-email TASK.md §3
CONTRACT, FROZEN @ v1). No IO, no retry/breaker needed: logs the fully-rendered message
at INFO via structlog and never raises. This is NOT a real delivery channel — selected
whenever email_smtp_enabled is False (today's out-of-the-box default).
"""

from __future__ import annotations

import structlog

from gateway.email.domain.entities import EmailMessage

_logger = structlog.get_logger(__name__)


class ConsoleEmailSender:
    """Implements EmailSender by logging the rendered mail. Never raises, no network."""

    async def send(self, message: EmailMessage) -> None:
        _logger.info(
            "console_email_dispatch",
            to=message.to,
            subject=message.subject,
            text_body=message.text_body,
        )
