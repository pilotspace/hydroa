"""Pure domain-string validation (domain-capture TASK.md §1 M3 — FROZEN @ v1).

Zero framework/IO imports (backend-architect discipline) — runs BEFORE any DB write (M3).
Deliberately mirrors the contract's exact prose: lowercased, trimmed, hostname-shaped labels
of [a-z0-9-] (1-63 chars each, no leading/trailing hyphen), >= 2 labels, total <= 253 chars,
not a bare IP literal.
"""

from __future__ import annotations

import ipaddress
import re

from gateway.domain_capture.domain.errors import DomainInvalidError

_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_MAX_DOMAIN_LENGTH = 253


def normalize_domain(raw: str) -> str:
    """Lowercase + trim + validate `raw` as a claimable hostname; raise DomainInvalidError
    on ANY failure (R1) — zero DB IO, called before any repository method."""
    candidate = (raw or "").strip().lower()

    if not candidate or len(candidate) > _MAX_DOMAIN_LENGTH:
        raise DomainInvalidError(f"domain must be 1-{_MAX_DOMAIN_LENGTH} chars: {raw!r}")

    labels = candidate.split(".")
    if len(labels) < 2:
        raise DomainInvalidError(f"domain must have >= 2 labels: {raw!r}")

    for label in labels:
        if not (1 <= len(label) <= 63) or not _LABEL_RE.match(label):
            raise DomainInvalidError(f"invalid hostname label {label!r} in domain {raw!r}")

    if _is_ip_literal(candidate):
        raise DomainInvalidError(f"domain must not be a bare IP literal: {raw!r}")

    return candidate


def _is_ip_literal(candidate: str) -> bool:
    try:
        ipaddress.ip_address(candidate)
        return True
    except ValueError:
        return False
