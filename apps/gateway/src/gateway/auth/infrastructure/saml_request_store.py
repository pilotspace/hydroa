"""Redis-backed SamlRequestStore — the server-side tenant-binding mechanism
that supersedes the OIDC cookie pattern (Ground R1, M3).

Design for failure (CLAUDE.md IO rule + TASK.md M12):
- FAIL-CLOSED: unlike this codebase's other Redis consumers (rate limiters,
  documented fail-OPEN for availability — see main.py:921-951 Ground note),
  this store is a correctness/security gate. Any RedisError/OSError/timeout
  is NEVER treated as "no pending request" — it raises SamlStoreUnavailableError
  so the caller maps it to 503 ERR_SAML_STORE_UNAVAILABLE (M12).
- Bounded 2-second timeout on every Redis call (asyncio.wait_for) — an
  unreachable Redis must never hang the login request indefinitely.
- GETDEL is a single atomic Redis command — the same call that answers "does
  this request exist" also enforces single-use (no separate check-then-delete
  race, M3 / concurrency scenario).
"""

from __future__ import annotations

import asyncio
import json
import uuid as _uuid_mod

from redis.exceptions import RedisError

from gateway.auth.domain.saml_entities import PendingSamlRequest
from gateway.auth.domain.saml_errors import SamlRequestAlreadyUsedError, SamlStoreUnavailableError

_REDIS_TIMEOUT_SECONDS = 2.0
_KEY_PREFIX = "saml:pending:"
_TOMBSTONE_PREFIX = "saml:pending-used:"
# Tombstone TTL: only needs to outlive the pending record's own 300s TTL by a
# margin (a replay attempt arriving right at the boundary must still see it).
_TOMBSTONE_TTL_SECONDS = 600


class RedisSamlRequestStore:
    """Redis GETDEL-backed pending-request store keyed by AuthnRequest ID.

    A short-lived tombstone (saml:pending-used:{request_id}) is written
    immediately after a successful GETDEL so a later replay attempt can be
    told apart from a genuinely-unknown request_id (M6 vs the replay Reject
    case) — see saml_ports.py::SamlRequestStore.get_and_delete docstring.
    The SECURITY property (at most one caller ever receives the record) does
    NOT depend on the tombstone; it holds from GETDEL's atomicity alone.
    """

    def __init__(self, redis: object) -> None:
        self._redis = redis

    def _key(self, request_id: str) -> str:
        return f"{_KEY_PREFIX}{request_id}"

    def _tombstone_key(self, request_id: str) -> str:
        return f"{_TOMBSTONE_PREFIX}{request_id}"

    async def put(self, request_id: str, record: PendingSamlRequest, *, ttl_seconds: int) -> None:
        payload = json.dumps(
            {
                "tenant_id": str(record.tenant_id),
                "sp_entity_id": record.sp_entity_id,
                "idp_entity_id": record.idp_entity_id,
                "created_at": record.created_at,
            }
        )
        try:
            await asyncio.wait_for(
                self._redis.set(self._key(request_id), payload, ex=ttl_seconds),  # type: ignore[union-attr]
                timeout=_REDIS_TIMEOUT_SECONDS,
            )
        except (RedisError, OSError, TimeoutError) as exc:
            raise SamlStoreUnavailableError(
                f"saml pending-request store unavailable (put): {exc}"
            ) from exc

    async def get_and_delete(self, request_id: str) -> PendingSamlRequest | None:
        try:
            raw: bytes | str | None = await asyncio.wait_for(
                self._redis.getdel(self._key(request_id)),  # type: ignore[union-attr]
                timeout=_REDIS_TIMEOUT_SECONDS,
            )
        except (RedisError, OSError, TimeoutError) as exc:
            raise SamlStoreUnavailableError(
                f"saml pending-request store unavailable (get_and_delete): {exc}"
            ) from exc

        if raw is None:
            # Ambiguous by construction (GETDEL can't tell "never existed" from
            # "already deleted") — disambiguate via the tombstone.
            try:
                was_used: bool = bool(
                    await asyncio.wait_for(
                        self._redis.exists(self._tombstone_key(request_id)),  # type: ignore[union-attr]
                        timeout=_REDIS_TIMEOUT_SECONDS,
                    )
                )
            except (RedisError, OSError, TimeoutError) as exc:
                raise SamlStoreUnavailableError(
                    f"saml pending-request store unavailable (tombstone check): {exc}"
                ) from exc
            if was_used:
                raise SamlRequestAlreadyUsedError(
                    f"saml pending request {request_id!r} was already consumed"
                )
            return None

        # Successful consumption — write the tombstone so a later replay of
        # this exact request_id is reported as ALREADY_USED, not NOT_FOUND.
        try:
            await asyncio.wait_for(
                self._redis.set(  # type: ignore[union-attr]
                    self._tombstone_key(request_id), "1", ex=_TOMBSTONE_TTL_SECONDS
                ),
                timeout=_REDIS_TIMEOUT_SECONDS,
            )
        except (RedisError, OSError, TimeoutError) as exc:
            # The record was already consumed by THIS call (we hold it) — a
            # tombstone-write failure must not turn a successful login into
            # ERR_SAML_STORE_UNAVAILABLE. Best-effort only; log and continue.
            import logging

            logging.getLogger(__name__).warning(
                "saml_request_store: tombstone write failed (non-fatal): %s", exc
            )

        text = raw.decode() if isinstance(raw, bytes) else raw
        try:
            data = json.loads(text)
            return PendingSamlRequest(
                tenant_id=_uuid_mod.UUID(data["tenant_id"]),
                sp_entity_id=data["sp_entity_id"],
                idp_entity_id=data["idp_entity_id"],
                created_at=data["created_at"],
            )
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            # A malformed stored record is a correctness gate too — fail closed
            # (never resurrect a request from unparseable state).
            raise SamlStoreUnavailableError(
                f"saml pending-request store returned malformed record: {exc}"
            ) from exc
