"""Curated static block-list of public / generic email-provider domains
(member-verified-recognition TASK.md §3 — FROZEN @ v1, R-sec-5).

A member-verified code proves control of ONE mailbox; allowing member-verify on a public
provider (e.g. gmail.com) would let a single user invite-by-domain the entire provider
population. Issuance is SKIPPED (silent) at signup for these domains and REFUSED with a
typed ERR_DOMAIN_GENERIC (422) at the resend endpoint. Deliberately a small, extensible,
zero-IO pure-function seam — extend the frozenset as new providers appear.
"""

from __future__ import annotations

# Lower-cased, normalized apex domains. Extensible — additive edits only.
PUBLIC_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "msn.com",
        "yahoo.com",
        "ymail.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "proton.me",
        "protonmail.com",
        "aol.com",
        "gmx.com",
        "gmx.net",
        "mail.com",
        "yandex.com",
        "yandex.ru",
        "zoho.com",
        "fastmail.com",
        "pm.me",
    }
)


def is_public_email_domain(domain: str) -> bool:
    """True iff `domain` is a known public/generic email provider (case-insensitive)."""
    return (domain or "").strip().lower() in PUBLIC_EMAIL_DOMAINS
