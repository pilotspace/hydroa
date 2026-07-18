"""SAML login use cases — SP-initiated flow (login init) + Assertion Consumer
Service (acs) orchestration.

Mirrors auth/application/use_cases.py's OidcLoginUseCase orchestration SHAPE
(state/binding check -> validate -> domain mapping -> provision -> mint) but
is a fully separate class per the §1 Framings decision (structurally
different trust-establishment steps — XML-signature verification vs
JWKS/RS256, request-store-pinned tenant vs cookie-pinned tenant).

Security posture (TASK.md §3 CONTRACT, freeze decision #2):
  - wantAssertionsSigned=True, wantMessagesSigned=False (assertion always
    signed; response-level signature validated only if present).
  - strict=True always — this is what activates the OneLogin toolkit's
    Audience/Issuer/Conditions timestamp/SubjectConfirmation checks; without
    it those checks silently do not run.
  - validate_num_assertions() (called unconditionally inside is_valid(),
    library-internal) rejects any document containing more than one
    <saml:Assertion> — the library's structural defense against the classic
    XML Signature Wrapping "duplicate assertion" payload (Ground R2, M4).
  - Claims (NameID, Attributes, assertion @ID) are read via the library's
    getters, which operate on the (sole) parsed Assertion node — combined
    with validate_num_assertions()==1, there is no ambiguity about which
    node the verified <ds:Signature> covers (M4 XSW defense).
"""

from __future__ import annotations

import asyncio
import time
import urllib.parse
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from onelogin.saml2.authn_request import OneLogin_Saml2_Authn_Request
from onelogin.saml2.constants import OneLogin_Saml2_Constants
from onelogin.saml2.errors import OneLogin_Saml2_ValidationError
from onelogin.saml2.response import OneLogin_Saml2_Response
from onelogin.saml2.settings import OneLogin_Saml2_Settings
from onelogin.saml2.utils import OneLogin_Saml2_Utils
from onelogin.saml2.xml_utils import OneLogin_Saml2_XML

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.auth.application.use_cases import SSO_PASSWORD_HASH_SENTINEL
from gateway.auth.domain.saml_entities import PendingSamlRequest
from gateway.auth.domain.saml_errors import (
    SamlAssertionExpiredError,
    SamlAssertionReplayedError,
    SamlAudienceMismatchError,
    SamlEmailMissingError,
    SamlIssuerMismatchError,
    SamlNotConfiguredError,
    SamlRequestNotFoundError,
    SamlResponseMismatchError,
    SamlSignatureInvalidError,
    SamlTenantConflictError,
)
from gateway.domain_capture.application.verified_domain_resolution import (
    resolve_verified_tenant_for_raw_domain,
)
from gateway.tenants.domain.entities import Role

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from gateway.auth.domain.saml_entities import SamlProviderConfig
    from gateway.auth.domain.saml_ports import SamlConfigResolver, SamlReplayCache, SamlRequestStore
    from gateway.core.config import Settings
    from gateway.domain_capture.domain.ports import DomainClaimResolver
    from gateway.tenants.domain.ports import IdentityRepository, TokenService

# Explicit exception-type -> ERR_SAML_* code mapping for audit metadata (TASK.md
# §2 Reject list) — NEVER derived from the class name (a naive
# `type(exc).__name__.upper()` transform drops the underscores the contract's
# codes require, e.g. SamlSignatureInvalidError -> "SAMLSIGNATUREINVALID"
# instead of "ERR_SAML_SIGNATURE_INVALID"; keeping this table authoritative
# avoids silently emitting a wrong code the frozen contract does not name).
_ERROR_CODE_BY_TYPE: dict[type[Exception], str] = {
    SamlSignatureInvalidError: "ERR_SAML_SIGNATURE_INVALID",
    SamlIssuerMismatchError: "ERR_SAML_ISSUER_MISMATCH",
    SamlAudienceMismatchError: "ERR_SAML_AUDIENCE_MISMATCH",
    SamlResponseMismatchError: "ERR_SAML_RESPONSE_MISMATCH",
    SamlAssertionExpiredError: "ERR_SAML_ASSERTION_EXPIRED",
}

PENDING_TTL_SECONDS = 300  # matches OIDC's _STATE_NONCE_MAX_AGE (M1)
_REPLAY_CACHE_TTL_CAP_SECONDS = 86400  # M5.6: min(NotOnOrAfter - now, 24h)

_NAMEID_FORMAT_EMAIL = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
_FALLBACK_ATTR_NAMES = {"email", "mail", "emailaddress"}

# Errors raised by OneLogin_Saml2_ValidationError.code that map to each ERR_SAML_* —
# see onelogin.saml2.errors.OneLogin_Saml2_ValidationError for the full enum.
_SIGNATURE_CODES = frozenset(
    {
        OneLogin_Saml2_ValidationError.INVALID_SIGNATURE,
        OneLogin_Saml2_ValidationError.NO_SIGNATURE_FOUND,
        OneLogin_Saml2_ValidationError.NO_SIGNED_ASSERTION,
        OneLogin_Saml2_ValidationError.NO_SIGNED_MESSAGE,
        OneLogin_Saml2_ValidationError.WRONG_SIGNED_ELEMENT,
        OneLogin_Saml2_ValidationError.ID_NOT_FOUND_IN_SIGNED_ELEMENT,
        OneLogin_Saml2_ValidationError.DUPLICATED_ID_IN_SIGNED_ELEMENTS,
        OneLogin_Saml2_ValidationError.INVALID_SIGNED_ELEMENT,
        OneLogin_Saml2_ValidationError.DUPLICATED_REFERENCE_IN_SIGNED_ELEMENTS,
        OneLogin_Saml2_ValidationError.UNEXPECTED_SIGNED_ELEMENTS,
        # XSW duplicate-assertion payload:
        OneLogin_Saml2_ValidationError.WRONG_NUMBER_OF_ASSERTIONS,
        OneLogin_Saml2_ValidationError.WRONG_NUMBER_OF_SIGNATURES,
        OneLogin_Saml2_ValidationError.WRONG_NUMBER_OF_SIGNATURES_IN_RESPONSE,
        OneLogin_Saml2_ValidationError.WRONG_NUMBER_OF_SIGNATURES_IN_ASSERTION,
        OneLogin_Saml2_ValidationError.UNSUPPORTED_SAML_VERSION,
        OneLogin_Saml2_ValidationError.MISSING_ID,
        OneLogin_Saml2_ValidationError.INVALID_XML_FORMAT,
    }
)
_ISSUER_CODES = frozenset(
    {
        OneLogin_Saml2_ValidationError.WRONG_ISSUER,
        OneLogin_Saml2_ValidationError.ISSUER_MULTIPLE_IN_RESPONSE,
        OneLogin_Saml2_ValidationError.ISSUER_NOT_FOUND_IN_ASSERTION,
    }
)
_AUDIENCE_CODES = frozenset({OneLogin_Saml2_ValidationError.WRONG_AUDIENCE})
_RESPONSE_MISMATCH_CODES = frozenset(
    {
        OneLogin_Saml2_ValidationError.WRONG_INRESPONSETO,
        OneLogin_Saml2_ValidationError.WRONG_SUBJECTCONFIRMATION,
        OneLogin_Saml2_ValidationError.WRONG_DESTINATION,
        OneLogin_Saml2_ValidationError.EMPTY_DESTINATION,
    }
)
_EXPIRED_CODES = frozenset(
    {
        OneLogin_Saml2_ValidationError.ASSERTION_TOO_EARLY,
        OneLogin_Saml2_ValidationError.ASSERTION_EXPIRED,
        OneLogin_Saml2_ValidationError.SESSION_EXPIRED,
        OneLogin_Saml2_ValidationError.RESPONSE_EXPIRED,
    }
)


def _validate_relative_redirect(path: str | None, default: str) -> str:
    """RelayState / post-login redirect MUST be a same-origin relative path —
    NEVER an absolute URL (open-redirect) and NEVER carries tenant identity
    (M1: "an optional RelayState carrying ONLY a same-origin relative
    post-login redirect path (never tenant identity)").
    """
    if not path:
        return default
    parsed = urllib.parse.urlparse(path)
    if parsed.scheme or parsed.netloc or not path.startswith("/") or path.startswith("//"):
        return default
    return path


def _security_settings() -> dict[str, Any]:
    """Freeze decision #2: assertion always signed; response signature only
    validated if present. strict=True (set by the caller) activates the
    Audience/Issuer/Conditions/SubjectConfirmation checks this dict alone
    does not control.
    """
    return {
        "wantAssertionsSigned": True,
        "wantMessagesSigned": False,
        "wantNameIdEncrypted": False,
        "wantAttributeStatement": False,
        "requestedAuthnContext": False,
        "signMetadata": False,
        "authnRequestsSigned": False,
    }


def _settings_dict(config: SamlProviderConfig) -> dict[str, Any]:
    return {
        "strict": True,
        "sp": {
            "entityId": config.sp_entity_id,
            "assertionConsumerService": {
                "url": config.acs_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "NameIDFormat": _NAMEID_FORMAT_EMAIL,
        },
        "idp": {
            "entityId": config.idp_entity_id,
            "singleSignOnService": {
                "url": config.idp_sso_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": config.idp_x509_cert,
        },
        "security": _security_settings(),
    }


class SamlLoginInitUseCase:
    """M1: SP-initiated login — resolve tenant config, mint an AuthnRequest,
    pin it server-side, redirect to the IdP.

    Routing (domain-routing-unification TASK.md §3 M1/M3/M8 — FROZEN @
    v2/CR-v2): the tenant is resolved EXCLUSIVELY via the verified
    tenant_domain_claims source of truth (normalize_domain +
    resolve_verified_tenant — the one shared predicate), then the SAML
    config is loaded by tenant_id — NEVER by an email_domains containment
    match. An admin-typed email_domains entry confers NO routing without a
    same-domain verified claim. CR-v2 reverted the new 403
    SAML_DOMAIN_NOT_MAPPED code v1 introduced here: every fail-closed
    rejection below reuses the EXISTING 404 SamlNotConfiguredError — SAML
    never had a per-tenant env-mapping fallback the way OIDC does, so there
    is no second trusted source to fall back to, and no new code is needed.
    """

    def __init__(
        self,
        *,
        config_resolver: SamlConfigResolver,
        request_store: SamlRequestStore,
        claim_resolver: DomainClaimResolver,
    ) -> None:
        self._config_resolver = config_resolver
        self._request_store = request_store
        self._claim_resolver = claim_resolver

    async def execute(self, *, domain: str | None, relay_state: str | None) -> str:
        """Returns the full 302 redirect URL to the IdP SSO endpoint.

        Raises SamlNotConfiguredError (404, EXISTING code — M3/R1, CR-v2) when
        no domain is supplied, the domain has no verified claim, or the
        claimed tenant has no enabled SAML config — all three are the SAME
        fail-closed rejection (no oracle distinguishing "unclaimed" from
        "claimed but unconfigured" from "nothing supplied").
        """
        if domain is None:
            raise SamlNotConfiguredError("no domain supplied to SAML login-init")

        mapped_tenant_id = await resolve_verified_tenant_for_raw_domain(
            self._claim_resolver, domain
        )
        if mapped_tenant_id is None:
            raise SamlNotConfiguredError(f"no verified domain claim for {domain!r}")

        config = await self._config_resolver.resolve_by_tenant_id(str(mapped_tenant_id))
        if config is None:
            # Claimed tenant has no enabled SAML config — same fail-closed
            # rejection as an unclaimed domain (no oracle between the two).
            raise SamlNotConfiguredError(
                f"verified tenant for {domain!r} has no enabled SAML config"
            )

        onelogin_settings = OneLogin_Saml2_Settings(
            _settings_dict(config), sp_validation_only=False
        )
        authn_request = OneLogin_Saml2_Authn_Request(onelogin_settings)
        request_id = authn_request.get_id()

        record = PendingSamlRequest(
            tenant_id=config.tenant_id,
            sp_entity_id=config.sp_entity_id,
            idp_entity_id=config.idp_entity_id,
            created_at=datetime.now(UTC).isoformat(),
        )
        await self._request_store.put(request_id, record, ttl_seconds=PENDING_TTL_SECONDS)

        params: dict[str, str] = {"SAMLRequest": authn_request.get_request(deflate=True)}
        safe_relay_state = _validate_relative_redirect(relay_state, default="")
        if safe_relay_state:
            params["RelayState"] = safe_relay_state
        return f"{config.idp_sso_url}?{urllib.parse.urlencode(params)}"


class SamlAcsUseCase:
    """M3-M9, M12, M13: validate the IdP's SAMLResponse and mint a session."""

    def __init__(
        self,
        *,
        config_resolver: SamlConfigResolver,
        request_store: SamlRequestStore,
        replay_cache: SamlReplayCache,
        repository: IdentityRepository,
        tokens: TokenService,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._config_resolver = config_resolver
        self._request_store = request_store
        self._replay_cache = replay_cache
        self._repository = repository
        self._tokens = tokens
        self._settings = settings
        self._session_factory = session_factory
        # M5.5: the OneLogin toolkit's Conditions/SubjectConfirmationData
        # NotBefore/NotOnOrAfter checks (inside is_valid()'s strict-mode
        # block) read this PROCESS-WIDE class attribute rather than taking a
        # per-call parameter. GATEWAY_SAML_CLOCK_SKEW_SECONDS is itself a
        # single global (not per-tenant) setting, so setting it once here —
        # not per-request — is correct and race-free for concurrent /acs
        # calls across different tenants (they all want the SAME skew).
        OneLogin_Saml2_Constants.ALLOWED_CLOCK_DRIFT = settings.saml_clock_skew_seconds  # pyright: ignore[reportAttributeAccessIssue]  — pyright infers Literal[300] from the untyped library's class-body default; a plain int reassignment is a correct, deliberate override

    def _peek_in_response_to(self, saml_response_b64: str) -> str | None:
        """M3: extract the top-level (still-UNVERIFIED) InResponseTo — used
        ONLY as a lookup key into the pending-request store. Parsed with the
        library's own defused-XML reader (forbid_dtd/forbid_entities) — never
        used for any authorization/identity decision (that only happens after
        full M5 signature-backed validation below).

        Raises SamlRequestNotFoundError on malformed base64/XML — there is no
        traceable pending request for input we cannot even parse.
        """
        try:
            raw = OneLogin_Saml2_Utils.b64decode(saml_response_b64)
            document = OneLogin_Saml2_XML.to_etree(raw)
        except Exception as exc:
            raise SamlRequestNotFoundError(f"malformed SAMLResponse: {exc}") from exc
        request_id = document.get("InResponseTo")
        if not request_id:
            return None
        return str(request_id)

    async def _audit(
        self,
        *,
        tenant_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        email: str | None,
        idp_entity_id: str | None,
        result: str,
        error_code: str | None,
    ) -> None:
        """M9: every /acs outcome (success + every M5/M6 rejection) is
        audited. AuditEvent's frozen audit_missing_actor invariant (a
        tenant-scoped event needs actor_user_id/actor_key_id) cannot be
        satisfied for a rejection before a user is resolved — those rows use
        tenant_id=None (system-scoped) and carry the known tenant_id (if any)
        in metadata instead, deliberately, to respect the frozen shape rather
        than weaken/bypass it (recorded in TASK.md §5).
        """
        metadata: dict[str, Any] = {"idp_entity_id": idp_entity_id}
        if error_code is not None:
            metadata["error_code"] = error_code

        event_tenant_id = tenant_id if user_id is not None else None
        if user_id is None and tenant_id is not None:
            metadata["tenant_id"] = str(tenant_id)

        asyncio.ensure_future(  # noqa: RUF006
            record_audit(
                self._session_factory,
                AuditEvent(
                    id=uuid.uuid4(),
                    tenant_id=event_tenant_id,
                    actor_user_id=user_id,
                    actor_email=email,
                    action="auth.saml_login",
                    target_type="user" if user_id else None,
                    target_id=str(user_id) if user_id else None,
                    result=result,
                    metadata=metadata,
                    created_at=datetime.now(UTC),
                ),
            )
        )

    def _map_validation_error(self, exc: OneLogin_Saml2_ValidationError) -> Exception:
        code = exc.code
        if code in _SIGNATURE_CODES:
            return SamlSignatureInvalidError(str(exc))
        if code in _ISSUER_CODES:
            return SamlIssuerMismatchError(str(exc))
        if code in _AUDIENCE_CODES:
            return SamlAudienceMismatchError(str(exc))
        if code in _RESPONSE_MISMATCH_CODES:
            return SamlResponseMismatchError(str(exc))
        if code in _EXPIRED_CODES:
            return SamlAssertionExpiredError(str(exc))
        # Any other structural validation failure (missing Conditions, missing
        # AuthnStatement, etc.) is closest in kind to a malformed/untrustworthy
        # assertion — fold into the signature-invalid rejection rather than
        # inventing an uncontracted error code.
        return SamlSignatureInvalidError(str(exc))

    def _verify_scd_in_response_to(
        self, response: OneLogin_Saml2_Response, request_id: str
    ) -> None:
        """M5.4 (the trust-bearing check — HARD-STOP fix, add-verify Finding 1,
        2026-07-10): independently re-verify that the SIGNED
        SubjectConfirmationData/@InResponseTo is PRESENT and EQUAL to the M3
        request_id, AFTER response.is_valid() has already succeeded.

        Why this is needed even though is_valid() runs strict-mode checks:
        python3-saml's own SubjectConfirmation loop
        (onelogin/saml2/response.py ~line 247-249) treats InResponseTo as
        OPTIONAL — `if in_response_to and irt and irt != in_response_to:
        continue` only rejects a MISMATCH, never an ABSENCE. Omitting
        InResponseTo from SubjectConfirmationData is a legal SAML variant
        (e.g. IdP-initiated SSO), so `is_valid()` alone lets a validly-signed
        assertion through with NO cryptographic binding to any specific
        pending request — an attacker holding any such assertion for a
        victim can wrap it in a fresh envelope pointing at a
        self-initiated pending request and mint a session as the victim.

        This reads the SCD via `_query_assertion`, python3-saml's own
        Reference/URI-scoped accessor — the SAME XSW-safe accessor the
        library uses internally for every other post-validation claim read
        (audience, issuer, attributes, NameID) — so this reads from the
        node the verified <ds:Signature> actually covers, never a raw
        unverified peek (M4's XSW defense applies here too). This is a
        plain attribute comparison on already-cryptographically-verified
        data, not a re-implementation of any XML-signature/crypto logic.

        Raises SamlResponseMismatchError unless at least one
        SubjectConfirmationData node on the verified assertion carries an
        InResponseTo equal to request_id.
        """
        # library's own XSW-safe (signed-Reference-scoped) assertion accessor;
        # no public equivalent exists for reading SubjectConfirmationData
        # post-validation.
        scd_nodes = response._query_assertion(  # pyright: ignore[reportPrivateUsage]
            "/saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData"
        )
        for scd in scd_nodes:
            irt = scd.get("InResponseTo")
            if irt and irt == request_id:
                return
        raise SamlResponseMismatchError(
            "signed SubjectConfirmationData/@InResponseTo is missing or does "
            f"not match the pending request_id {request_id!r}"
        )

    def _resolve_email(
        self, response: OneLogin_Saml2_Response, config: SamlProviderConfig
    ) -> str | None:
        """M13: ranked lookup — (1) NameID when Format=emailAddress; (2) the
        per-tenant configured attribute name; (3) a case-insensitive
        email/mail/emailaddress fallback search."""
        nameid_format = response.get_nameid_format()
        nameid = response.get_nameid()
        if nameid and nameid_format == _NAMEID_FORMAT_EMAIL:
            return nameid.lower()

        attributes = response.get_attributes()
        configured_name = config.email_attribute_name
        if attributes.get(configured_name):
            return attributes[configured_name][0].lower()

        friendly_attributes = response.get_friendlyname_attributes()
        for name, values in {**attributes, **friendly_attributes}.items():
            if name.lower() in _FALLBACK_ATTR_NAMES and values:
                return values[0].lower()

        return None

    async def execute(
        self, *, saml_response_b64: str, relay_state: str | None
    ) -> tuple[str, int, str]:
        """Returns (jwt_token, expires_in, redirect_path).

        The Destination anchor used for `is_valid()`'s Destination check is
        ALWAYS the canonical `settings.saml_acs_url` — NEVER the raw incoming
        request URL (which is Host-header derived and attacker-influenceable
        on a misconfigured deployment); this is a stricter, safer default
        than trusting request.url.
        """
        request_id = self._peek_in_response_to(saml_response_b64)
        if request_id is None:
            await self._audit(
                tenant_id=None,
                user_id=None,
                email=None,
                idp_entity_id=None,
                result="rejected",
                error_code="ERR_SAML_REQUEST_NOT_FOUND",
            )
            raise SamlRequestNotFoundError("SAMLResponse has no InResponseTo (M6)")

        # M3: single Redis call answers "does this exist" AND enforces
        # single-use (GETDEL) — may raise SamlRequestNotFoundError /
        # SamlRequestAlreadyUsedError / SamlStoreUnavailableError, all
        # propagated as-is to the caller (router maps them to HTTP).
        pending = await self._request_store.get_and_delete(request_id)
        if pending is None:
            await self._audit(
                tenant_id=None,
                user_id=None,
                email=None,
                idp_entity_id=None,
                result="rejected",
                error_code="ERR_SAML_REQUEST_NOT_FOUND",
            )
            raise SamlRequestNotFoundError(f"no pending request for {request_id!r}")

        config = await self._config_resolver.resolve_by_tenant_id(str(pending.tenant_id))
        if config is None:
            # The tenant's config was disabled/deleted between /login and
            # /acs — treat identically to "never configured" (M1's byte-
            # identical no-op posture extends to this narrow race).
            await self._audit(
                tenant_id=pending.tenant_id,
                user_id=None,
                email=None,
                idp_entity_id=pending.idp_entity_id,
                result="rejected",
                error_code="ERR_SAML_NOT_CONFIGURED",
            )
            raise SamlNotConfiguredError(f"tenant {pending.tenant_id} SAML config disabled/removed")

        onelogin_settings = OneLogin_Saml2_Settings(
            _settings_dict(config), sp_validation_only=False
        )
        acs_destination = self._settings.saml_acs_url
        request_data = {
            "https": "on" if acs_destination.startswith("https://") else "off",
            "http_host": urllib.parse.urlparse(acs_destination).netloc,
            "script_name": urllib.parse.urlparse(acs_destination).path,
            "post_data": {"SAMLResponse": saml_response_b64},
        }
        response = OneLogin_Saml2_Response(onelogin_settings, saml_response_b64)

        try:
            response.is_valid(request_data, request_id=request_id, raise_exceptions=True)
        except OneLogin_Saml2_ValidationError as exc:
            mapped: Exception = self._map_validation_error(exc)
            await self._audit(
                tenant_id=pending.tenant_id,
                user_id=None,
                email=None,
                idp_entity_id=pending.idp_entity_id,
                result="rejected",
                error_code=_ERROR_CODE_BY_TYPE.get(type(mapped), "ERR_SAML_SIGNATURE_INVALID"),
            )
            raise mapped from exc
        except Exception as exc:
            # Any OTHER unexpected failure while parsing/validating an
            # attacker-controlled document (malformed structure the library's
            # own checks did not anticipate) fails CLOSED as a signature/
            # structural rejection rather than a 500 — never a silent accept.
            mapped = SamlSignatureInvalidError(f"unexpected validation failure: {exc}")
            await self._audit(
                tenant_id=pending.tenant_id,
                user_id=None,
                email=None,
                idp_entity_id=pending.idp_entity_id,
                result="rejected",
                error_code=_ERROR_CODE_BY_TYPE.get(type(mapped), "ERR_SAML_SIGNATURE_INVALID"),
            )
            raise mapped from exc

        # M5.4 (HARD-STOP fix): independently re-verify the SIGNED
        # SubjectConfirmationData/@InResponseTo — is_valid() above does NOT
        # enforce this when the SCD simply omits InResponseTo (see
        # _verify_scd_in_response_to docstring). This runs OUTSIDE the
        # try/except above so a SamlResponseMismatchError is never
        # re-wrapped by the generic "unexpected validation failure" handler.
        try:
            self._verify_scd_in_response_to(response, request_id)
        except SamlResponseMismatchError:
            await self._audit(
                tenant_id=pending.tenant_id,
                user_id=None,
                email=None,
                idp_entity_id=pending.idp_entity_id,
                result="rejected",
                error_code="ERR_SAML_RESPONSE_MISMATCH",
            )
            raise

        # M5.6: independent second replay layer, keyed by assertion @ID.
        assertion_id = response.get_assertion_id()
        not_on_or_after = response.get_assertion_not_on_or_after()
        ttl = _REPLAY_CACHE_TTL_CAP_SECONDS
        if not_on_or_after:
            ttl = min(int(not_on_or_after - time.time()), _REPLAY_CACHE_TTL_CAP_SECONDS)
        is_new = await self._replay_cache.mark_consumed_if_new(assertion_id, ttl_seconds=ttl)
        if not is_new:
            await self._audit(
                tenant_id=pending.tenant_id,
                user_id=None,
                email=None,
                idp_entity_id=pending.idp_entity_id,
                result="rejected",
                error_code="ERR_SAML_ASSERTION_REPLAYED",
            )
            raise SamlAssertionReplayedError(f"assertion {assertion_id!r} already consumed")

        # M13: ranked email resolution.
        email = self._resolve_email(response, config)
        if not email:
            await self._audit(
                tenant_id=pending.tenant_id,
                user_id=None,
                email=None,
                idp_entity_id=pending.idp_entity_id,
                result="rejected",
                error_code="ERR_SAML_EMAIL_MISSING",
            )
            raise SamlEmailMissingError("no email resolvable via NameID/attribute/fallback lookup")

        # M7: JIT provisioning — ALWAYS role=member for new users; an
        # existing user's stored role is preserved (never downgraded).
        try:
            user = await self._repository.get_or_provision_saml_user(
                email=email,
                tenant_id=pending.tenant_id,
                password_hash=SSO_PASSWORD_HASH_SENTINEL,
            )
        except SamlTenantConflictError:
            await self._audit(
                tenant_id=pending.tenant_id,
                user_id=None,
                email=email,
                idp_entity_id=pending.idp_entity_id,
                result="rejected",
                error_code="ERR_SAML_TENANT_CONFLICT",
            )
            raise

        # M8: mint via the UNCHANGED TokenService.issue seam — same shape a
        # password/OIDC login produces.
        jwt_token, expires_in = self._tokens.issue(
            user_id=user.id,
            tenant_id=user.tenant_id,
            role=user.role,
            email=user.email,
        )

        await self._audit(
            tenant_id=user.tenant_id,
            user_id=user.id,
            email=user.email,
            idp_entity_id=pending.idp_entity_id,
            result="success",
            error_code=None,
        )

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
                        metadata={"role": "superadmin", "auth_method": "saml"},
                        created_at=datetime.now(UTC),
                    ),
                )
            )

        redirect_path = _validate_relative_redirect(
            relay_state, default=self._settings.saml_post_login_redirect
        )
        return jwt_token, expires_in, redirect_path
