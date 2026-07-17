"""RED->GREEN suite for the transactional-email seam (transactional-email TASK.md §4).

One test per §2 SCENARIOS entry (backend scope; the 3 dashboard-side scenarios — M11/M12 —
live in apps/dashboard/tests/invite-member-dialog-email.test.tsx), plus a small number of
well-justified contract-conformance additions (exact response/dataclass shape checks).

MUST run RED (missing implementation) before Build — `gateway.email` does not exist yet, so
every import below is a true MODULE_NOT_FOUND, the established true-red convention this
codebase already uses (see tests/member_invite_issuance's own docstring).

No real SMTP/network is ever touched: adapter tests monkeypatch `smtplib.SMTP` with an
in-process fake; wiring tests use FakeEmailSender (tests/transactional_email/conftest.py) or
the smtp_app_client fixture (a real SmtpEmailSender is SELECTED by build_email_sender, then
immediately swapped for a fake on app.state — proving the SELECTION logic, not the socket).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.transactional_email.conftest import FakeEmailSender, seed_tenant

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _poll_until(predicate: Any, *, timeout_s: float = 5.0, interval_s: float = 0.05) -> Any:
    """Poll an async predicate until truthy or timeout_s elapses.

    A fire-and-forget task (asyncio.ensure_future) completes strictly AFTER the HTTP
    response returns — a fixed `asyncio.sleep(0.05)` flakes under a load-shared host
    (see tests/credits_ledger/conftest.py's own poll_until + the [[fire-and-forget audit
    test flake]] this mirrors). Poll instead of guessing a delay.
    """
    deadline = time.monotonic() + timeout_s
    last: Any = None
    while time.monotonic() < deadline:
        last = await predicate()
        if last:
            return last
        await asyncio.sleep(interval_s)
    raise AssertionError(f"_poll_until: condition not met within {timeout_s}s (last={last!r})")


class _FakeSmtpFactory:
    """Drop-in replacement for smtplib.SMTP — no real socket ever opened.

    Call `monkeypatch.setattr("smtplib.SMTP", factory)`. `factory(host, port, timeout=...)`
    raises `fail_exc` for the first `fail_first_n` invocations, then returns `self` (a
    context manager recording starttls/login/sendmail calls).
    """

    def __init__(
        self,
        fail_first_n: int = 0,
        fail_exc: Callable[[str], BaseException] = OSError,
    ) -> None:
        self.fail_first_n = fail_first_n
        self.fail_exc = fail_exc
        self.attempts = 0
        self.starttls_called = False
        self.login_called_with: tuple[str, str] | None = None
        self.sendmail_called_with: tuple[str, list[str], str] | None = None

    def __call__(self, host: str, port: int, timeout: float | None = None) -> _FakeSmtpFactory:
        self.attempts += 1
        if self.attempts <= self.fail_first_n:
            raise self.fail_exc("simulated failure")
        return self

    def __enter__(self) -> _FakeSmtpFactory:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def starttls(self) -> None:
        self.starttls_called = True

    def login(self, user: str, password: str) -> None:
        self.login_called_with = (user, password)

    def sendmail(self, from_addr: str, to_addrs: list[str], msg: str) -> None:
        self.sendmail_called_with = (from_addr, to_addrs, msg)


# ---------------------------------------------------------------------------
# TEST 1: EmailSender Protocol is adapter-agnostic                                      [M1]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_sender_protocol_is_adapter_agnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.core.config import Settings
    from gateway.email.domain.entities import EmailMessage
    from gateway.email.domain.ports import EmailSender
    from gateway.email.infrastructure.console_email_sender import ConsoleEmailSender
    from gateway.email.infrastructure.smtp_email_sender import SmtpEmailSender

    # EmailMessage carries no invite-specific field.
    msg = EmailMessage(to="a@b.io", subject="hi", text_body="body", html_body=None)
    assert {f.name for f in msg.__dataclass_fields__.values()} == {  # type: ignore[attr-defined]
        "to",
        "subject",
        "text_body",
        "html_body",
    }

    console = ConsoleEmailSender()
    fake_smtp = _FakeSmtpFactory()
    monkeypatch.setattr("smtplib.SMTP", fake_smtp)
    smtp = SmtpEmailSender(Settings(email_smtp_enabled=False))

    assert isinstance(console, EmailSender)
    assert isinstance(smtp, EmailSender)

    async def _dispatch(sender: EmailSender, message: EmailMessage) -> None:
        # Caller holds only an EmailSender-typed reference — no concrete adapter import.
        await sender.send(message)

    await _dispatch(console, msg)
    await _dispatch(smtp, msg)
    assert fake_smtp.attempts == 1  # smtp adapter actually ran the (faked) send path


# ---------------------------------------------------------------------------
# TEST 2: console adapter logs the rendered mail                                        [M2]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_console_adapter_logs_rendered_mail_never_raises() -> None:
    from structlog.testing import capture_logs

    from gateway.email.domain.entities import EmailMessage
    from gateway.email.infrastructure.console_email_sender import ConsoleEmailSender

    sender = ConsoleEmailSender()
    msg = EmailMessage(
        to="new@co.io", subject="You're invited", text_body="join us", html_body=None
    )

    with capture_logs() as logs:
        await sender.send(msg)  # never raises, no network

    assert any(
        entry.get("log_level") == "info" and entry.get("to") == "new@co.io" for entry in logs
    ), f"expected an INFO log with the recipient; got {logs}"
    assert any("You're invited" in str(v) for entry in logs for v in entry.values())


# ---------------------------------------------------------------------------
# TEST 3: smtp adapter sends over a timed, retried, breaker-guarded connection    [M2, M10]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smtp_adapter_retries_transient_failure_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.core.config import Settings
    from gateway.email.domain.entities import EmailMessage
    from gateway.email.infrastructure.smtp_email_sender import SmtpEmailSender

    fake = _FakeSmtpFactory(fail_first_n=1, fail_exc=OSError)
    monkeypatch.setattr("smtplib.SMTP", fake)

    settings = Settings(
        email_smtp_enabled=True,
        email_smtp_host="smtp.example.com",
        email_smtp_max_retries=2,
        email_smtp_timeout_seconds=1.0,
        dashboard_public_origin="https://app.example.com",
    )
    sender = SmtpEmailSender(settings)
    msg = EmailMessage(to="x@co.io", subject="s", text_body="b", html_body=None)

    await sender.send(msg)  # must NOT raise — retried once, then succeeded

    assert fake.attempts == 2, "one failed attempt + one successful retry"
    assert fake.starttls_called is True
    assert fake.sendmail_called_with is not None


@pytest.mark.asyncio
async def test_smtp_adapter_exhausts_retries_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.core.config import Settings
    from gateway.email.domain.entities import EmailMessage
    from gateway.email.domain.errors import EmailSendError
    from gateway.email.infrastructure.smtp_email_sender import SmtpEmailSender

    fake = _FakeSmtpFactory(fail_first_n=999, fail_exc=OSError)  # always fails
    monkeypatch.setattr("smtplib.SMTP", fake)

    settings = Settings(
        email_smtp_enabled=True,
        email_smtp_host="smtp.example.com",
        email_smtp_max_retries=2,
        dashboard_public_origin="https://app.example.com",
    )
    sender = SmtpEmailSender(settings)
    msg = EmailMessage(to="x@co.io", subject="s", text_body="b", html_body=None)

    with pytest.raises(EmailSendError):
        await sender.send(msg)

    assert fake.attempts == 3, "1 initial + 2 retries (email_smtp_max_retries), then give up"


# ---------------------------------------------------------------------------
# TEST 3b: isolated CircuitBreaker instance, per adapter instance                       [M10]
# ---------------------------------------------------------------------------


def test_smtp_adapter_owns_an_isolated_circuit_breaker_instance() -> None:
    from gateway.core.config import Settings
    from gateway.email.infrastructure.smtp_email_sender import SmtpEmailSender
    from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

    settings = Settings(
        email_smtp_enabled=True,
        email_smtp_host="smtp.example.com",
        dashboard_public_origin="https://app.example.com",
    )
    a = SmtpEmailSender(settings)
    b = SmtpEmailSender(settings)

    # White-box structural check: two instances must not share a breaker.
    a_breaker = a._breaker  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    b_breaker = b._breaker  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert isinstance(a_breaker, CircuitBreaker)
    assert a_breaker is not b_breaker, "each SmtpEmailSender must own its OWN breaker"


@pytest.mark.asyncio
async def test_smtp_adapter_circuit_open_short_circuits_without_a_new_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After enough consecutive failures the breaker opens; a further send() raises
    immediately (guard() fails) WITHOUT attempting another (faked) connection.
    """
    from gateway.core.config import Settings
    from gateway.email.domain.entities import EmailMessage
    from gateway.email.domain.errors import EmailSendError
    from gateway.email.infrastructure.smtp_email_sender import SmtpEmailSender

    fake = _FakeSmtpFactory(fail_first_n=999, fail_exc=OSError)
    monkeypatch.setattr("smtplib.SMTP", fake)

    settings = Settings(
        email_smtp_enabled=True,
        email_smtp_host="smtp.example.com",
        email_smtp_max_retries=0,  # 1 attempt per send() call -> 1 breaker failure per call
        dashboard_public_origin="https://app.example.com",
    )
    sender = SmtpEmailSender(settings)
    msg = EmailMessage(to="x@co.io", subject="s", text_body="b", html_body=None)

    # Drive 5 consecutive failures to trip the breaker (CircuitBreaker._FAILURE_THRESHOLD).
    for _ in range(5):
        with pytest.raises(EmailSendError):
            await sender.send(msg)

    attempts_before = fake.attempts
    with pytest.raises(EmailSendError):
        await sender.send(msg)  # breaker OPEN — must short-circuit
    assert fake.attempts == attempts_before, "circuit-open must NOT attempt a new connection"


# ---------------------------------------------------------------------------
# TEST 4: console is the default adapter when SMTP is unconfigured                      [M3]
# ---------------------------------------------------------------------------


def test_build_email_sender_defaults_to_console() -> None:
    from gateway.core.config import Settings
    from gateway.email.infrastructure.console_email_sender import ConsoleEmailSender
    from gateway.email.infrastructure.smtp_email_sender import SmtpEmailSender
    from gateway.main import build_email_sender

    settings = Settings()  # email_smtp_enabled defaults False
    sender = build_email_sender(settings)
    assert isinstance(sender, ConsoleEmailSender)
    assert not isinstance(sender, SmtpEmailSender)


# ---------------------------------------------------------------------------
# TEST 5: smtp enabled selects the smtp adapter                                         [M3]
# ---------------------------------------------------------------------------


def test_build_email_sender_selects_smtp_when_enabled() -> None:
    from gateway.core.config import Settings
    from gateway.email.infrastructure.smtp_email_sender import SmtpEmailSender
    from gateway.main import build_email_sender

    settings = Settings(
        email_smtp_enabled=True,
        email_smtp_host="smtp.example.com",
        dashboard_public_origin="https://app.example.com",
    )
    sender = build_email_sender(settings)
    assert isinstance(sender, SmtpEmailSender)


# ---------------------------------------------------------------------------
# TEST 6: configured-but-empty SMTP host boot-errors                              [M4, R2]
# ---------------------------------------------------------------------------


def test_settings_boot_errors_on_empty_smtp_host_when_enabled() -> None:
    from gateway.core.config import Settings

    with pytest.raises(ValueError) as exc_info:
        Settings(
            email_smtp_enabled=True,
            email_smtp_host="",
            dashboard_public_origin="https://app.example.com",
        )
    msg = str(exc_info.value)
    assert "GATEWAY_EMAIL_SMTP_HOST" in msg
    assert "GATEWAY_EMAIL_SMTP_ENABLED" in msg


# ---------------------------------------------------------------------------
# TEST 7: SMTP enabled without a dashboard origin boot-errors                     [M5, R3]
# ---------------------------------------------------------------------------


def test_settings_boot_errors_on_empty_dashboard_origin_when_smtp_enabled() -> None:
    from gateway.core.config import Settings

    with pytest.raises(ValueError) as exc_info:
        Settings(
            email_smtp_enabled=True,
            email_smtp_host="smtp.example.com",
            dashboard_public_origin="",
        )
    msg = str(exc_info.value)
    assert "GATEWAY_DASHBOARD_PUBLIC_ORIGIN" in msg
    assert "GATEWAY_EMAIL_SMTP_ENABLED" in msg


# ---------------------------------------------------------------------------
# TEST 8: default config (console + no origin) never boot-errors                        [M6]
# ---------------------------------------------------------------------------


def test_default_config_never_boot_errors_and_console_link_is_relative() -> None:
    from gateway.core.config import Settings
    from gateway.email.application.invite_email_template import render_invite_email

    settings = Settings()  # no kwargs — today's out-of-the-box shape
    assert settings.email_smtp_enabled is False
    assert settings.dashboard_public_origin == ""

    message = render_invite_email(
        to="a@b.io", role="member", token="abc123", origin=settings.dashboard_public_origin
    )
    assert "/invite/abc123" in message.text_body
    assert "https://" not in message.text_body  # no origin prefix when unset


# ---------------------------------------------------------------------------
# TEST 9: dashboard_public_origin set builds an absolute link                    [M6, M8]
# ---------------------------------------------------------------------------


def test_render_invite_email_builds_absolute_link_with_origin() -> None:
    from gateway.email.application.invite_email_template import render_invite_email

    message = render_invite_email(
        to="a@b.io", role="member", token="abc123", origin="https://app.hydroa.example"
    )
    assert "https://app.hydroa.example/invite/abc123" in message.text_body
    # Matches the dashboard's own client-side ${window.location.origin}/invite/${token}.
    link = next(line for line in message.text_body.splitlines() if "/invite/" in line)
    assert link.strip() == "https://app.hydroa.example/invite/abc123"


# ---------------------------------------------------------------------------
# TEST 10: an email send failure never fails invite creation                    [M7, R1]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_send_failure_never_fails_invite_creation(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    app: Any,
) -> None:
    app.state.email_sender = FakeEmailSender(raises=RuntimeError("smtp exploded"))
    headers = _bearer(seeded_tenant["owner_token"])

    r = await client.post(
        "/admin/invites", json={"email": "new@co.io", "role": "member"}, headers=headers
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["token"], "the full InviteCreateResponse body is unaffected by the email failure"
    assert set(body.keys()) == {
        "id",
        "email",
        "role",
        "status",
        "expires_at",
        "created_at",
        "invited_by_user_id",
        "token",
        "email_delivery_channel",
    }
    await asyncio.sleep(0.05)  # let the fire-and-forget email task run+swallow


# ---------------------------------------------------------------------------
# TEST 11: invite email dispatch is independent of the audit dispatch                   [M8]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_email_dispatch_independent_of_audit_dispatch(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    app: Any,
    db_session: AsyncSession,
) -> None:
    app.state.email_sender = FakeEmailSender(raises=RuntimeError("always fails"))
    headers = _bearer(seeded_tenant["owner_token"])

    r = await client.post(
        "/admin/invites", json={"email": "x@co.io", "role": "member"}, headers=headers
    )
    assert r.status_code == 201, r.text
    # Pins this test to the actual new wiring (M9) so it cannot pass vacuously before the
    # email seam exists — a truly independent-dispatch property alone would hold even
    # with zero email code, since nothing would call app.state.email_sender at all.
    assert r.json()["email_delivery_channel"] == "console"

    async def _fetch_create_audit() -> Any:
        return (
            await db_session.execute(
                text(
                    "SELECT action FROM audit_events WHERE action = 'invite.create' "
                    "ORDER BY created_at DESC LIMIT 1"
                )
            )
        ).fetchone()

    create_audit = await _poll_until(_fetch_create_audit)
    assert create_audit is not None, "the failed email send must not prevent the audit write"


# ---------------------------------------------------------------------------
# TEST 12: re-invite dispatches a fresh email for the new token                  [M8 edge]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reinvite_dispatches_fresh_email_for_new_token(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    app: Any,
) -> None:
    fake = FakeEmailSender()
    app.state.email_sender = fake
    headers = _bearer(seeded_tenant["owner_token"])

    r1 = await client.post(
        "/admin/invites", json={"email": "x@co.io", "role": "member"}, headers=headers
    )
    assert r1.status_code == 201, r1.text
    token_1 = r1.json()["token"]

    r2 = await client.post(
        "/admin/invites", json={"email": "x@co.io", "role": "member"}, headers=headers
    )
    assert r2.status_code == 201, r2.text
    token_2 = r2.json()["token"]
    assert token_1 != token_2

    async def _both_sent() -> bool:
        return len(fake.sent) >= 2

    await _poll_until(_both_sent)

    assert len(fake.sent) == 2
    assert any(token_1 in m.text_body for m in fake.sent)
    assert any(token_2 in m.text_body for m in fake.sent)
    # The old token's (already-sent) link is not re-sent by THIS re-invite dispatch.
    second_message = next(m for m in fake.sent if token_2 in m.text_body)
    assert token_1 not in second_message.text_body


# ---------------------------------------------------------------------------
# TEST 13: response carries the additive delivery-channel field ("smtp")               [M9]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_reports_smtp_channel_when_enabled(
    smtp_app_client: tuple[Any, httpx.AsyncClient, FakeEmailSender],
) -> None:
    smtp_app, smtp_client, fake = smtp_app_client
    tenant = await seed_tenant(smtp_client, smtp_app, suffix="smtp")
    headers = _bearer(tenant["owner_token"])

    r = await smtp_client.post(
        "/admin/invites", json={"email": "new@co.io", "role": "member"}, headers=headers
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email_delivery_channel"] == "smtp"
    # Every other field is byte-identical in shape to before this task.
    assert set(body.keys()) - {"email_delivery_channel"} == {
        "id",
        "email",
        "role",
        "status",
        "expires_at",
        "created_at",
        "invited_by_user_id",
        "token",
    }

    async def _one_sent() -> bool:
        return len(fake.sent) >= 1

    await _poll_until(_one_sent)
    assert len(fake.sent) == 1


# ---------------------------------------------------------------------------
# TEST 14: response reports console channel by default                                 [M9]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_reports_console_channel_by_default(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    app: Any,
) -> None:
    app.state.email_sender = FakeEmailSender()
    headers = _bearer(seeded_tenant["owner_token"])

    r = await client.post(
        "/admin/invites", json={"email": "new2@co.io", "role": "member"}, headers=headers
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email_delivery_channel"] == "console"
    assert set(body.keys()) - {"email_delivery_channel"} == {
        "id",
        "email",
        "role",
        "status",
        "expires_at",
        "created_at",
        "invited_by_user_id",
        "token",
    }


# ---------------------------------------------------------------------------
# TEST 15: a non-transient SMTP error is never retried                                  [R4]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_transient_smtp_error_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    import smtplib

    from gateway.core.config import Settings
    from gateway.email.domain.entities import EmailMessage
    from gateway.email.domain.errors import EmailSendError
    from gateway.email.infrastructure.smtp_email_sender import SmtpEmailSender

    fake = _FakeSmtpFactory(
        fail_first_n=999,
        fail_exc=lambda *_a, **_kw: smtplib.SMTPAuthenticationError(535, b"bad credentials"),
    )
    # `fail_exc` must be a raisable callable — patch __call__ to raise the real exception type.
    monkeypatch.setattr("smtplib.SMTP", fake)

    settings = Settings(
        email_smtp_enabled=True,
        email_smtp_host="smtp.example.com",
        email_smtp_max_retries=2,
        dashboard_public_origin="https://app.example.com",
    )
    sender = SmtpEmailSender(settings)
    msg = EmailMessage(to="x@co.io", subject="s", text_body="b", html_body=None)

    with pytest.raises(EmailSendError):
        await sender.send(msg)

    assert fake.attempts == 1, "SMTPAuthenticationError must never be retried"


# ---------------------------------------------------------------------------
# TEST 16: invalid SMTP timeout/retry settings boot-error                               [R5]
# ---------------------------------------------------------------------------


def test_invalid_smtp_timeout_boots_errors() -> None:
    from gateway.core.config import Settings

    with pytest.raises(ValueError):
        Settings(email_smtp_timeout_seconds=0)


def test_invalid_smtp_max_retries_boot_errors() -> None:
    from gateway.core.config import Settings

    with pytest.raises(ValueError):
        Settings(email_smtp_max_retries=-1)


# ---------------------------------------------------------------------------
# TEST 17 (concurrency edge case): two concurrent invite creates each get their own
# isolated send — a slow/failing send for one never blocks or fails the other's HTTP
# response.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_concurrent_invite_creates_each_get_isolated_send(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    app: Any,
) -> None:
    # A generous delay + a loose relative margin (elapsed < half the delay) — this only
    # needs to prove the HTTP response returns WITHOUT waiting on the slow send, which
    # holds at any delay; sized to tolerate a heavily load-shared CI/dev host rather than
    # racing a tight absolute bound.
    _SLOW_DELAY_S = 2.0

    class _SlowThenFailSender:
        def __init__(self) -> None:
            self.sent: list[Any] = []

        async def send(self, message: Any) -> None:
            if message.to == "slow@co.io":
                await asyncio.sleep(_SLOW_DELAY_S)
                self.sent.append(message)
            else:
                raise RuntimeError("boom — always fails for this recipient")

    sender = _SlowThenFailSender()
    app.state.email_sender = sender
    headers = _bearer(seeded_tenant["owner_token"])

    start = time.monotonic()
    r1, r2 = await asyncio.gather(
        client.post(
            "/admin/invites", json={"email": "slow@co.io", "role": "member"}, headers=headers
        ),
        client.post(
            "/admin/invites", json={"email": "fail@co.io", "role": "member"}, headers=headers
        ),
    )
    elapsed = time.monotonic() - start

    assert r1.status_code == 201, r1.text
    assert r2.status_code == 201, r2.text
    assert elapsed < _SLOW_DELAY_S / 2, (
        f"HTTP responses must return before the slow ({_SLOW_DELAY_S}s) send completes"
        f" — took {elapsed}s"
    )

    async def _slow_sent() -> bool:
        return len(sender.sent) >= 1

    await _poll_until(_slow_sent, timeout_s=_SLOW_DELAY_S + 5.0)
    assert len(sender.sent) == 1
    assert sender.sent[0].to == "slow@co.io"


# ---------------------------------------------------------------------------
# TEST 18 (contract-conformance): the wiring calls send_email via asyncio.ensure_future,
# never awaited on the hot path — proven by response latency staying independent of the
# fake sender's artificial delay (already exercised above); this test additionally proves
# `send_email` itself (application layer) swallows exceptions and never raises.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email_application_wrapper_swallows_all_exceptions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from gateway.email.application.email_dispatch import send_email
    from gateway.email.domain.entities import EmailMessage

    class _AlwaysRaises:
        async def send(self, message: EmailMessage) -> None:
            raise RuntimeError("boom")

    msg = EmailMessage(to="a@b.io", subject="s", text_body="b", html_body=None)
    with caplog.at_level(logging.WARNING):
        await send_email(_AlwaysRaises(), msg)  # must NOT raise

    assert any("email" in r.message.lower() for r in caplog.records)
