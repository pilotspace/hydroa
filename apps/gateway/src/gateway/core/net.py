"""Shared trusted-client-IP resolution (edge-input-hardening TASK.md §3 Part A FROZEN @ v1).

Behind Envoy (``use_remote_address: true``, no ``xff_num_trusted_hops`` configured), Envoy
APPENDS its own view of the peer address as the LAST token of ``X-Forwarded-For`` — it does
NOT strip client-supplied tokens ahead of it. The RIGHTMOST token is therefore the one hop
the gateway can trust; the LEFTMOST token is attacker-controlled (a client can prepend
arbitrary fake hops). Trusting the leftmost token (the pre-fix behavior of the 3 duplicated
``_resolve_client_ip`` helpers this module replaces) lets an attacker rotate through
synthetic identities to bypass any per-IP rate limiter.

Security: this resolver NEVER raises — every failure mode (no XFF header, fewer tokens than
``trusted_hops``, an unparseable selected token) fails CLOSED to ``request.client.host``,
never to an earlier, client-supplied hop.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request


def resolve_trusted_client_ip(request: Request, trusted_hops: int) -> str:
    """Return the trusted client IP, trusting only the last ``trusted_hops`` XFF token(s).

    Args:
        request: the inbound FastAPI/Starlette request.
        trusted_hops: number of trusted hops Envoy (or an equivalent front proxy) appends
            to ``X-Forwarded-For``. Read from ``Settings.trusted_proxy_hops`` by each call
            site — never hardcoded here.

    Returns:
        The IP string to use as the caller's identity for rate-limiting / audit purposes.

    Resolution:
        1. No ``X-Forwarded-For`` header -> ``request.client.host`` (or ``"unknown"`` when
           ``request.client`` is also ``None`` — byte-identical to the pre-fix helpers).
        2. ``X-Forwarded-For`` present -> split on ``","``, strip each token; select
           ``tokens[len(tokens) - trusted_hops]`` (the token ``trusted_hops`` positions from
           the right). If that index is negative (fewer tokens than ``trusted_hops``, i.e. a
           truncated/tampered header) OR the selected token fails
           ``ipaddress.ip_address()`` parsing, fall back to ``request.client.host`` — fail
           closed, NEVER trust an earlier (more client-controlled) hop just because the
           expected one is missing or malformed.
    """
    # Combine EVERY ``X-Forwarded-For`` header LINE, not just the first. A client can send
    # two distinct ``X-Forwarded-For:`` lines; ``Headers.get()`` returns only the first
    # (attacker-controlled) one, which would leave Envoy's own appended hop — sent as a
    # separate line — unread. Joining all lines with ``,`` reconstructs the full ordered
    # chain so the rightmost token is still the hop Envoy appended last.
    xff = ",".join(part for part in request.headers.getlist("x-forwarded-for") if part)
    if xff:
        tokens = [t.strip() for t in xff.split(",")]
        index = len(tokens) - trusted_hops
        if index >= 0:
            candidate = tokens[index]
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                pass
            else:
                return candidate
    if request.client is not None:
        return request.client.host
    return "unknown"


__all__ = ["resolve_trusted_client_ip"]
