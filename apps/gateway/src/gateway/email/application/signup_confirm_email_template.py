"""render_signup_confirm_email — the pending-personal-signup confirmation email
(scoped-self-serve-signup TASK.md §3 CONTRACT M8 — FROZEN @ v1, SECURITY). Pure function,
no IO. Carries the RAW confirm token; mirrors render_invite_email's origin-optional link
construction exactly.
"""

from __future__ import annotations

from gateway.email.domain.entities import EmailMessage


def render_signup_confirm_email(
    *, to: str, tenant_name: str, token: str, origin: str
) -> EmailMessage:
    """Build the signup-confirm EmailMessage.

    link = f"{origin}/signup/confirm?token={token}" if origin else
    f"/signup/confirm?token={token}" — matches render_invite_email's own
    origin-optional link construction exactly.
    """
    link = f"{origin}/signup/confirm?token={token}" if origin else f"/signup/confirm?token={token}"
    subject = "Confirm your Hydroa account"
    text_body = f"Confirm your account for {tenant_name}.\n\n{link}\n"
    return EmailMessage(to=to, subject=subject, text_body=text_body, html_body=None)
