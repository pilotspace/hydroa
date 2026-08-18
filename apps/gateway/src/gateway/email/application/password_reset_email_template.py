"""render_password_reset_email — the password-reset link email
(auth-hardening-login-sessions TASK.md §3 CONTRACT M2/M3 — FROZEN @ v1, SECURITY).
Pure function, no IO. Carries the RAW reset token (its ONLY existence outside the
sha256-at-rest row); mirrors render_signup_confirm_email's origin-optional link
construction exactly.
"""

from __future__ import annotations

from gateway.email.domain.entities import EmailMessage


def render_password_reset_email(*, to: str, token: str, origin: str) -> EmailMessage:
    """Build the password-reset EmailMessage.

    link = f"{origin}/reset-password?token={token}" if origin else
    f"/reset-password?token={token}" — matches render_signup_confirm_email's own
    origin-optional link construction exactly.
    """
    link = f"{origin}/reset-password?token={token}" if origin else f"/reset-password?token={token}"
    subject = "Reset your Hydroa password"
    text_body = (
        "A password reset was requested for this address.\n\n"
        f"{link}\n\n"
        "If you did not request this, you can ignore this email — your password is unchanged.\n"
    )
    return EmailMessage(to=to, subject=subject, text_body=text_body, html_body=None)
