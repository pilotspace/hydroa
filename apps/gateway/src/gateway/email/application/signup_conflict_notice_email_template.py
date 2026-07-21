"""render_signup_conflict_notice_email — the out-of-band "you already have an account"
notice (scoped-self-serve-signup TASK.md §3 CONTRACT M9 — FROZEN @ v1, SECURITY). Pure
function, no IO. Generic and link-free (R-scope-1: no forgot-password/reset flow exists
in this codebase to offer) — names no other detail about the account.
"""

from __future__ import annotations

from gateway.email.domain.entities import EmailMessage


def render_signup_conflict_notice_email(*, to: str, origin: str) -> EmailMessage:
    """Build the conflict-notice EmailMessage — no token, no reset link.

    login_link = f"{origin}/login" if origin else "/login" — matches
    render_domain_verified_email's own origin-optional link construction exactly.
    """
    login_link = f"{origin}/login" if origin else "/login"
    subject = "You already have a Hydroa account"
    text_body = (
        "An account with this email address already exists. "
        f"If this was you, log in here: {login_link}\n\n"
        "If you didn't request this, you can safely ignore this email.\n"
    )
    return EmailMessage(to=to, subject=subject, text_body=text_body, html_body=None)
