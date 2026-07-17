"""SmtpEmailSender — the config-gated EmailSender adapter (transactional-email TASK.md
§3 CONTRACT, FROZEN @ v1). Real outbound IO via stdlib smtplib, run off the event loop
via asyncio.to_thread. Owns its OWN isolated CircuitBreaker instance (ml-moderation-layer
fold: an ancillary IO seam never shares a breaker with the proxy's or any other seam's) and
a tenacity-bounded retry limited to transient/connection errors only (R4: auth failures /
recipients-refused are never retried).

Failure policy (design-for-failure, PROJECT.md invariant):
  - explicit socket timeout (email_smtp_timeout_seconds)
  - bounded retry (email_smtp_max_retries), transient-only (OSError / SMTPConnectError /
    SMTPServerDisconnected) — a non-retryable error (auth, recipients-refused, ...) fails
    the FIRST attempt only
  - an isolated CircuitBreaker guards every send(); OPEN -> raise EmailSendError WITHOUT
    attempting a new connection (mirrors gateway.objectstore.s3.S3ObjectStore._guard
    exactly — a guard() failure is not itself a new upstream failure, so it is never
    double-counted via on_upstream_error())
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage as _MimeMessage

from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt

from gateway.core.config import Settings
from gateway.email.domain.entities import EmailMessage
from gateway.email.domain.errors import EmailSendError
from gateway.proxy.domain.errors import CircuitOpenError
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker


def _is_retryable(exc: BaseException) -> bool:
    """Transient/connection-level failures only (R4).

    NOTE: every smtplib.SMTPException subclass (SMTPAuthenticationError,
    SMTPRecipientsRefused, ...) is ALSO an OSError subclass as of Python 3.4+
    (smtplib.SMTPException derives from OSError) — a bare
    ``isinstance(exc, OSError)`` predicate would therefore accidentally retry a
    permanent auth failure or a recipients-refused response. SMTPConnectError and
    SMTPServerDisconnected are the two SMTPException subclasses that genuinely
    represent a connection-level failure (never a completed, rejected send) and are
    retried; every OTHER SMTPException is a protocol-level outcome — the send WAS
    attempted and rejected — and is never retried. A raw (non-SMTPException) OSError
    (ConnectionRefusedError, socket timeout, ...) is a pure transport failure and IS
    retried.
    """
    if isinstance(exc, (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected)):
        return True
    if isinstance(exc, smtplib.SMTPException):
        return False
    return isinstance(exc, OSError)


def _send_sync(settings: Settings, message: EmailMessage) -> None:
    """Blocking smtplib call — always run via asyncio.to_thread, never on the event loop."""
    with smtplib.SMTP(
        settings.email_smtp_host,
        settings.email_smtp_port,
        timeout=settings.email_smtp_timeout_seconds,
    ) as smtp:
        if settings.email_smtp_use_tls:
            smtp.starttls()
        if settings.email_smtp_username:
            smtp.login(
                settings.email_smtp_username,
                settings.email_smtp_password.get_secret_value(),
            )

        mime = _MimeMessage()
        mime["Subject"] = message.subject
        mime["From"] = settings.email_smtp_from_address
        mime["To"] = message.to
        mime.set_content(message.text_body)
        if message.html_body:
            mime.add_alternative(message.html_body, subtype="html")

        smtp.sendmail(settings.email_smtp_from_address, [message.to], mime.as_string())


class SmtpEmailSender:
    """Implements EmailSender over stdlib smtplib — timeout + bounded retry + breaker."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._breaker = CircuitBreaker()  # OWN instance — never shared with any other seam.

    def _guard(self) -> None:
        try:
            self._breaker.guard()
        except CircuitOpenError as exc:
            raise EmailSendError("email circuit is open") from exc

    async def send(self, message: EmailMessage) -> None:
        self._guard()
        settings = self._settings
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(settings.email_smtp_max_retries + 1),
                retry=retry_if_exception(_is_retryable),
                reraise=True,
            ):
                with attempt:
                    await asyncio.to_thread(_send_sync, settings, message)
        except Exception as exc:
            # Exhausted transient retries OR a non-retryable error (auth failure,
            # recipients refused, ...) — either way, one failure against the breaker.
            self._breaker.on_upstream_error()
            raise EmailSendError(str(exc)) from exc
        else:
            self._breaker.record_success()
