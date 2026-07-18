"""OIDC login use case — orchestrates the server-side authorization-code flow.

Flow:
  1. Validate state param against oidc_state cookie (CSRF binding)
  2. Exchange authorization code for tokens via OidcTokenExchanger
  3. Extract + validate ID token claims (iss, aud, exp, nonce, email)
  4. Domain mapping: map email domain → tenant_id
  5. Provision or look up user (always role=member)
  6. Mint ai_proxy_session JWT via TokenService

Security constraints (§3 inviolables):
  - ID token arrives ONLY from server-side httpx POST; never from browser input
  - Claim validation (iss, aud, exp, nonce, email) is always enforced
  - Auto-provisioned role is ALWAYS member — never from claims; the minted
    session carries the user's STORED role (a prior legitimate promotion is
    honored; SSO itself never grants owner/admin)
  - client_secret never logged
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import jwt
import structlog

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.auth.domain.errors import (
    OidcAccountDeactivatedError,
    OidcDomainNotMappedError,
    OidcInvalidCallbackError,
    OidcSessionExpiredError,
    OidcStateMismatchError,
    OidcTokenExpiredError,
    OidcTokenInvalidError,
    OidcUpstreamError,
)
from gateway.tenants.domain.entities import Role

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from gateway.auth.application.jwks_key_cache import JwksKeyCache
    from gateway.auth.domain.entities import DomainMapping, OidcProviderConfig
    from gateway.auth.domain.ports import JwksClient, OidcTokenExchanger
    from gateway.core.config import Settings
    from gateway.tenants.domain.ports import IdentityRepository, TokenService

logger = structlog.get_logger(__name__)

# WARNING sentinel — logged in non-dev environments to signal signature skip.
# Updated (v5): names GATEWAY_OIDC_JWKS_URL as the remedy.
# NEVER emitted when verification is active (jwks_client is not None).
_SIGNATURE_SKIP_WARNING = (
    "OIDC ID token signature NOT verified (RS256/JWKS verification INACTIVE). "
    "TLS channel to IdP token endpoint is the trust anchor (OIDC Core §3.1.3.7(6)). "
    "Set GATEWAY_OIDC_JWKS_URL to enable RS256 JWKS signature verification."
)

SSO_PASSWORD_HASH_SENTINEL = "!sso-no-password"  # noqa: S105 — sentinel string, not a real password


def _parse_domain_mappings(domain_mapping_json: str) -> list[DomainMapping]:
    """Parse GATEWAY_OIDC_DOMAIN_MAPPING JSON into DomainMapping objects.

    domain-routing-unification TASK.md §3 M4 (FROZEN @ v2/CR-v2): this env
    fallback is RETAINED — a verified tenant_domain_claims row always takes
    precedence (enforced structurally: /login resolves via the claim FIRST,
    per-tenant DB config second, and only pins the "env-config" cookie
    sentinel when NEITHER exists — see oidc_router.py). This parse helper
    feeds ONLY the callback's Step 6 `else:` fallback below.
    """
    from gateway.auth.domain.entities import DomainMapping

    try:
        raw: list[dict[str, str]] = json.loads(domain_mapping_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid GATEWAY_OIDC_DOMAIN_MAPPING JSON: {exc}") from exc
    return [
        DomainMapping(
            email_domain=entry["email_domain"],
            tenant_id=uuid.UUID(entry["tenant_id"]),
        )
        for entry in raw
    ]


def _decode_id_token_claims(id_token: str) -> dict[str, Any]:
    """Decode JWT claims without signature verification (v4 design — see §3).

    SECURITY NOTE: Signature verification is INTENTIONALLY skipped per
    OIDC Core 1.0 §3.1.3.7(6) — the token arrives via server-side httpx POST
    over TLS to the trusted token endpoint URL (from Settings, not request input).
    The nonce binding prevents replay. Full claim validation is still enforced.

    This approach is SPEC-SANCTIONED for this exact flow shape. v5 will add
    RS256 JWKS verification when cryptography is added to the allowlist.
    """
    try:
        # Decode without algorithm/key — extract claims only.
        # options must disable both signature and audience checks (we validate manually).
        claims: dict[str, Any] = jwt.decode(
            id_token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_exp": False,  # we validate exp manually for correct error code
            },
            algorithms=["HS256", "RS256"],
        )
        return claims
    except jwt.DecodeError as exc:
        raise OidcTokenInvalidError(f"Malformed ID token: {exc}") from exc


class OidcLoginUseCase:
    """Orchestrates the OIDC server-side authorization-code callback flow."""

    def __init__(
        self,
        exchanger: OidcTokenExchanger,
        repository: IdentityRepository,
        tokens: TokenService,
        settings: Settings,
        jwks_client: JwksClient | None = None,
        jwks_key_cache: JwksKeyCache | None = None,
        oidc_config: OidcProviderConfig | None = None,
        *,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._exchanger = exchanger
        self._repository = repository
        self._tokens = tokens
        self._settings = settings
        self._jwks_client = jwks_client
        # NEW (superadmin-audit-foundation TASK.md §3 Part C — FROZEN @ v1): used ONLY
        # to schedule the post-issuance audit write below — never for OIDC exchange,
        # claims validation, or user provisioning, which are byte-identical to before
        # this task. Keyword-only + required (no default) so no future caller can
        # silently skip the audit wiring — mirrors Part A's resolve_platform_credential
        # precedent. Placed after the existing defaulted params to preserve every
        # existing positional-or-keyword call shape unchanged; the sole construction
        # site (auth/api/deps.py::get_oidc_use_case_with_config) already passes every
        # argument by keyword.
        self._session_factory = session_factory
        # Per-tenant OidcProviderConfig (when DB-backed path is active).
        # When present, overrides oidc_issuer/oidc_client_id/oidc_jwks_url from settings.
        self._oidc_config = oidc_config
        # Activation rule (§3): jwks_client present ⇒ verification ACTIVE. A missing
        # cache must never silently deactivate verification — fall back to a local
        # per-use-case cache (correctness over hit rate) rather than to skip-verify.
        if jwks_client is not None and jwks_key_cache is None:
            from gateway.auth.application.jwks_key_cache import JwksKeyCache as _Cache

            jwks_key_cache = _Cache()
        self._jwks_key_cache = jwks_key_cache
        # domain-routing-unification §3 M4 (FROZEN @ v2/CR-v2): env
        # GATEWAY_OIDC_DOMAIN_MAPPING is a trusted OPERATOR-set fallback —
        # RETAINED, not deleted (v1 wholesale-deleted this and broke ~23
        # legacy env-routing tests). A verified tenant_domain_claims row
        # always takes precedence over it; Step 6 below only reaches this
        # fallback when no per-tenant DB config is pinned.
        self._domain_mappings = _parse_domain_mappings(settings.oidc_domain_mapping)

        # Log signature skip warning in non-dev environments ONLY when verification
        # is INACTIVE (jwks_client is None). Never emit on the active path.
        if jwks_client is None and settings.environment not in ("dev", "test"):
            logger.warning(_SIGNATURE_SKIP_WARNING)
            logging.getLogger(__name__).warning(_SIGNATURE_SKIP_WARNING)

    @property
    def _effective_issuer(self) -> str:
        """Return the issuer to validate against: per-tenant config or env Settings."""
        if self._oidc_config is not None:
            return self._oidc_config.issuer
        return self._settings.oidc_issuer

    @property
    def _effective_client_id(self) -> str:
        """Return the client_id to validate against: per-tenant config or env Settings."""
        if self._oidc_config is not None:
            return self._oidc_config.client_id
        return self._settings.oidc_client_id

    @property
    def _effective_jwks_url(self) -> str:
        """Return the JWKS URL to use: per-tenant config or env Settings."""
        if self._oidc_config is not None:
            return self._oidc_config.jwks_url
        return self._settings.oidc_jwks_url

    async def execute(
        self,
        *,
        code: str | None,
        state: str | None,
        cookie_state: str | None,
        cookie_nonce: str | None,
    ) -> tuple[str, int]:
        """Execute OIDC callback flow; returns (jwt_token, expires_in).

        Raises:
            OidcInvalidCallbackError  — code or state param missing
            OidcSessionExpiredError   — oidc_state cookie missing
            OidcStateMismatchError    — state param != oidc_state cookie
            OidcUpstreamError         — IdP token exchange failed
            OidcTokenInvalidError     — ID token claims invalid
            OidcTokenExpiredError     — ID token expired
            OidcDomainNotMappedError  — email domain not in mapping
            OidcTenantConflictError   — user exists in different tenant
        """
        # Step 1: validate query params
        if not code or not state:
            raise OidcInvalidCallbackError("Missing code or state query parameters")

        # Step 2: validate state cookie (CSRF binding)
        if cookie_state is None:
            raise OidcSessionExpiredError("oidc_state cookie absent — session expired")
        if not hmac.compare_digest(state, cookie_state):
            raise OidcStateMismatchError("State parameter does not match oidc_state cookie")

        # Step 3: exchange code for tokens
        # Also catch httpx exceptions here in case a FakeExchanger raises them
        # directly (e.g. FakeOidcExchanger(raise_exc=httpx.TimeoutException(...))).
        # The production HttpxOidcExchanger wraps these in OidcUpstreamError itself,
        # but we guard here so the use case is resilient to both patterns.
        try:
            token_response = await self._exchanger.exchange(
                code=code, redirect_uri=self._settings.oidc_redirect_uri
            )
        except OidcUpstreamError:
            raise
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise OidcUpstreamError(f"IdP token exchange failed: {exc}") from exc

        # Step 4: extract id_token
        id_token: str | None = token_response.get("id_token")
        if not id_token:
            raise OidcUpstreamError("id_token absent from IdP token endpoint response")

        # Step 5: decode + validate claims
        # Activation rule (§3): jwks_client presence alone decides — __init__
        # guarantees a cache exists whenever a client does, so a missing cache can
        # never flip this branch to skip-verify.
        if self._jwks_client is not None and self._jwks_key_cache is not None:
            # ── Active path: RS256/JWKS signature verification ────────────────────
            # 1. Peek at the JWT header to extract alg + kid (before any key fetch).
            try:
                header = jwt.get_unverified_header(id_token)
            except jwt.DecodeError as exc:
                raise OidcTokenInvalidError(f"Malformed ID token header: {exc}") from exc

            # 2. Algorithm allowlist enforcement — SECURITY INVARIANT.
            #    alg=none, HS256 (key-confusion), ES256, PS256 → all rejected.
            if header.get("alg") != "RS256":
                raise OidcTokenInvalidError(
                    f"ID token alg {header.get('alg')!r} rejected; only RS256 is accepted"
                )

            # 3. Resolve signing key via cache (kid-miss refresh inside JwksKeyCache).
            #    Cache key is (jwks_url, kid) — prevents cross-tenant kid collisions (T11).
            kid: str | None = header.get("kid")
            jwks_url = self._effective_jwks_url
            key = await self._jwks_key_cache.resolve(jwks_url, kid, self._jwks_client)

            # 4. Decode and verify signature + exp (pyjwt validates exp automatically).
            #    aud is validated manually downstream; issuer= not passed to jwt.decode.
            try:
                claims: dict[str, Any] = jwt.decode(
                    id_token,
                    key,
                    algorithms=["RS256"],
                    options={"verify_aud": False},
                )
            except jwt.ExpiredSignatureError as exc:
                raise OidcTokenExpiredError("ID token has expired") from exc
            except jwt.InvalidTokenError as exc:
                raise OidcTokenInvalidError(
                    f"ID token signature verification failed: {exc}"
                ) from exc
        else:
            # ── Inactive path: v4 TLS-channel mode (verify_signature=False) ───────
            # Governed by OIDC Core §3.1.3.7(6) sanction — all v4 preconditions
            # remain in force (server-side receipt, TLS, nonce binding, full claims).
            # The skip WARNING (naming GATEWAY_OIDC_JWKS_URL) was emitted in __init__.
            claims = _decode_id_token_claims(id_token)

        # Validate iss — use per-tenant config when available (tenant-confusion defense)
        iss: str = claims.get("iss", "")
        effective_issuer = self._effective_issuer
        if iss != effective_issuer:
            raise OidcTokenInvalidError(
                f"ID token iss mismatch: expected {effective_issuer!r}, got {iss!r}"
            )

        # Validate aud (can be str or list) — use per-tenant client_id
        effective_client_id = self._effective_client_id
        aud: str | list[str] = claims.get("aud", "")
        if isinstance(aud, str):
            aud_list = [aud]
        else:
            aud_list = list(aud)
        if effective_client_id not in aud_list:
            raise OidcTokenInvalidError(
                f"ID token aud does not contain client_id {effective_client_id!r}"
            )

        # Validate exp
        exp: int = claims.get("exp", 0)
        if int(time.time()) >= exp:
            raise OidcTokenExpiredError("ID token has expired")

        # Validate nonce
        token_nonce: str | None = claims.get("nonce")
        if token_nonce != cookie_nonce:
            raise OidcTokenInvalidError("ID token nonce does not match oidc_nonce cookie")

        # Validate email
        email_raw: str = claims.get("email", "")
        if not email_raw:
            raise OidcTokenInvalidError("ID token email claim absent or empty")
        email = email_raw.lower()

        # Step 6: tenant binding (domain-routing-unification TASK.md §3 M4 —
        # FROZEN @ v2/CR-v2). Precedence: a per-tenant DB config pinned at
        # /login (itself resolved claim-FIRST — see oidc_router.py M2) always
        # wins; ONLY when no per-tenant config is pinned does this fall back
        # to the operator-set GATEWAY_OIDC_DOMAIN_MAPPING env table. v1
        # deleted this fallback branch outright; CR-v2 restores it — a
        # verified claim still always wins because it is what produced the
        # pinned per-tenant config in the first place.
        domain = email.split("@", 1)[-1] if "@" in email else ""
        mapped_tenant_id: uuid.UUID | None = None

        if self._oidc_config is not None:
            # Per-tenant DB path: tenant_id is pinned by the per-tenant config.
            mapped_tenant_id = self._oidc_config.tenant_id
        else:
            # Env-Settings path: use GATEWAY_OIDC_DOMAIN_MAPPING (fallback only)
            for mapping in self._domain_mappings:
                if mapping.email_domain == domain:
                    mapped_tenant_id = mapping.tenant_id
                    break
            if mapped_tenant_id is None:
                raise OidcDomainNotMappedError(
                    f"Email domain {domain!r} not in GATEWAY_OIDC_DOMAIN_MAPPING"
                )

        # Step 7: provision or look up user (ALWAYS role=member)
        user = await self._repository.get_or_provision_oidc_user(
            email=email,
            tenant_id=mapped_tenant_id,
            password_hash=SSO_PASSWORD_HASH_SENTINEL,
        )

        # NEW (scim-provisioning TASK.md §3, M7 — FROZEN @ v1): a deactivated user's SSO
        # login is rejected — same denial family as the password-login path, no session
        # JWT is issued. Checked AFTER provisioning/lookup (an existing SCIM-deactivated
        # user is never re-activated by an SSO login) and BEFORE minting the JWT below.
        if user.deactivated_at is not None:
            raise OidcAccountDeactivatedError(f"User {email!r} is deactivated")

        # Step 8: mint session JWT with the user's STORED role.
        # Auto-provisioned users are always member (enforced in the repository),
        # so SSO never auto-grants owner/admin (§3). An EXISTING user promoted to
        # admin via legitimate admin action keeps that role — hardcoding MEMBER
        # here would silently downgrade their session on every SSO login.
        jwt_token, expires_in = self._tokens.issue(
            user_id=user.id,
            tenant_id=user.tenant_id,
            role=user.role,
            email=user.email,
        )

        # NEW (superadmin-audit-foundation TASK.md §3 Part C — FROZEN @ v1): fires iff
        # the STORED role being preserved into this JWT is superadmin — identical
        # firing rule to the /admin/auth password-login side (Part B, out of this
        # task's scope), evaluated post-issuance. get_or_provision_oidc_user above
        # always provisions a brand-new user as role=member (never from claims), so
        # this can only ever fire for an EXISTING superadmin matched by email — OIDC
        # never grants the role, it only audits a login by someone who already has it.
        # Fire-and-forget + fail-open: reuses record_audit's already-frozen contract
        # verbatim (own isolated session, swallow-all-exceptions-log-a-warning) — an
        # audit-subsystem failure must never block a superadmin's login response.
        if user.role == Role.SUPERADMIN:
            asyncio.ensure_future(  # noqa: RUF006
                record_audit(
                    self._session_factory,
                    AuditEvent(
                        id=uuid.uuid4(),
                        tenant_id=user.tenant_id,
                        actor_user_id=user.id,
                        actor_email=user.email,
                        action="auth.superadmin_login",
                        target_type="user",
                        target_id=str(user.id),
                        result="success",
                        metadata={"role": "superadmin", "auth_method": "oidc"},
                        created_at=datetime.now(UTC),
                    ),
                )
            )

        return jwt_token, expires_in
