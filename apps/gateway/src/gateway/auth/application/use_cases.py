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

import hmac
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

import httpx
import jwt
import structlog

from gateway.auth.domain.errors import (
    OidcDomainNotMappedError,
    OidcInvalidCallbackError,
    OidcSessionExpiredError,
    OidcStateMismatchError,
    OidcTokenExpiredError,
    OidcTokenInvalidError,
    OidcUpstreamError,
)

if TYPE_CHECKING:
    from gateway.auth.domain.entities import DomainMapping
    from gateway.auth.domain.ports import OidcTokenExchanger
    from gateway.core.config import Settings
    from gateway.tenants.domain.ports import IdentityRepository, TokenService

logger = structlog.get_logger(__name__)

# WARNING sentinel — logged in non-dev environments to signal signature skip
_SIGNATURE_SKIP_WARNING = (
    "OIDC ID token signature NOT verified (v4: cryptography package absent). "
    "TLS channel to IdP token endpoint is the trust anchor (OIDC Core §3.1.3.7(6)). "
    "Add cryptography to allowlist in v5 for RS256 JWKS verification."
)

SSO_PASSWORD_HASH_SENTINEL = "!sso-no-password"  # noqa: S105 — sentinel string, not a real password


def _parse_domain_mappings(domain_mapping_json: str) -> list[DomainMapping]:
    """Parse GATEWAY_OIDC_DOMAIN_MAPPING JSON into DomainMapping objects."""
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
    ) -> None:
        self._exchanger = exchanger
        self._repository = repository
        self._tokens = tokens
        self._settings = settings
        self._domain_mappings = _parse_domain_mappings(settings.oidc_domain_mapping)

        # Log signature skip warning in non-dev environments
        if settings.environment not in ("dev", "test"):
            logger.warning(_SIGNATURE_SKIP_WARNING)
            logging.getLogger(__name__).warning(_SIGNATURE_SKIP_WARNING)

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
        claims = _decode_id_token_claims(id_token)

        # Validate iss
        iss: str = claims.get("iss", "")
        if iss != self._settings.oidc_issuer:
            raise OidcTokenInvalidError(
                f"ID token iss mismatch: expected {self._settings.oidc_issuer!r}, got {iss!r}"
            )

        # Validate aud (can be str or list)
        aud: str | list[str] = claims.get("aud", "")
        if isinstance(aud, str):
            aud_list = [aud]
        else:
            aud_list = list(aud)
        if self._settings.oidc_client_id not in aud_list:
            raise OidcTokenInvalidError(
                f"ID token aud does not contain client_id {self._settings.oidc_client_id!r}"
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

        # Step 6: domain mapping
        domain = email.split("@", 1)[-1] if "@" in email else ""
        mapped_tenant_id: uuid.UUID | None = None
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

        return jwt_token, expires_in
