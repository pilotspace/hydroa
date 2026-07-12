"""Test helpers for building REAL, cryptographically signed SAML assertions.

Uses python3-saml's own OneLogin_Saml2_Utils.add_sign (xmlsec-backed) to sign
just the <saml:Assertion> element — the same shape a real IdP produces and
the same signing primitive the production code's validation counterpart
(OneLogin_Saml2_Response.is_valid) verifies. This is deliberately NOT a fake/
mock of SAML — it round-trips through the real crypto so a passing test means
the production signature-verification path is genuinely exercised.

Approach validated interactively before writing any production code (build
Assertion XML -> sign ONLY the Assertion sub-tree via add_sign -> splice into
the Response XML -> OneLogin_Saml2_Auth.process_response() accepts it).
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from onelogin.saml2.utils import OneLogin_Saml2_Utils

_SIGN_ALG = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
_DIGEST_ALG = "http://www.w3.org/2001/04/xmlenc#sha256"


@dataclass(frozen=True)
class IdpKeyPair:
    """A generated (private_key_pem, cert_pem) pair usable as an IdP signing identity."""

    key_pem: str
    cert_pem: str


def generate_idp_keypair(
    *, common_name: str = "fake-idp.test", expired: bool = False
) -> IdpKeyPair:
    """Generate a self-signed RSA keypair/cert — a fresh fake IdP identity per test."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    if expired:
        not_before = now - timedelta(days=730)
        not_after = now - timedelta(days=1)
    else:
        not_before = now - timedelta(days=1)
        not_after = now + timedelta(days=365)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    return IdpKeyPair(key_pem=key_pem, cert_pem=cert_pem)


def _saml_time(dt: datetime) -> str:
    return OneLogin_Saml2_Utils.parse_time_to_SAML(dt.timestamp())


@dataclass
class AssertionSpec:
    """Every knob a scenario needs to control when building a fake IdP response."""

    idp_entity_id: str
    sp_entity_id: str
    acs_url: str
    request_id: str | None  # None -> no InResponseTo (IdP-initiated / M6)
    subject_nameid: str = "user@acme.com"
    subject_nameid_format: str = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
    attributes: dict[str, list[str]] = field(default_factory=dict)
    not_before_offset_seconds: int = -60
    not_on_or_after_offset_seconds: int = 300
    # SubjectConfirmationData/@NotOnOrAfter is checked STRICTLY by python3-saml
    # with NO ALLOWED_CLOCK_DRIFT tolerance (response.py's is_valid(), unlike
    # validate_timestamps() for saml:Conditions, which does add the drift) — a
    # real discovery made while wiring up the clock-skew test (S17). Defaulted
    # to a value independent of not_on_or_after_offset_seconds so that varying
    # the Conditions offset (to test M5.5 skew tolerance) never accidentally
    # trips the SCD bearer-token check, which is a distinct, always-strict
    # security control (the SAML spec treats SCD windows as intentionally
    # short-lived, so this is correct product behavior, not a bug).
    scd_not_on_or_after_offset_seconds: int | None = None
    assertion_id: str = field(default_factory=lambda: "_" + uuid.uuid4().hex)
    response_id: str = field(default_factory=lambda: "_" + uuid.uuid4().hex)
    sign: bool = True
    sign_with: IdpKeyPair | None = None  # defaults to a fresh valid keypair if sign=True
    inject_xsw_evil_nameid: str | None = None  # M4 XSW scenario: second unsigned Assertion
    scd_in_response_to_override: str | None = None  # tamper the SIGNED InResponseTo directly
    audience_override: str | None = None  # tamper the signed Audience directly
    issuer_override: str | None = None  # tamper the signed Issuer directly


def _attribute_statement_xml(attributes: dict[str, list[str]]) -> str:
    if not attributes:
        return ""
    attrs_xml = ""
    for name, values in attributes.items():
        values_xml = "".join(f"<saml:AttributeValue>{v}</saml:AttributeValue>" for v in values)
        attrs_xml += f'<saml:Attribute Name="{name}">{values_xml}</saml:Attribute>'
    return f"<saml:AttributeStatement>{attrs_xml}</saml:AttributeStatement>"


def _build_assertion_xml(spec: AssertionSpec, *, assertion_id: str, nameid: str) -> str:
    now = datetime.now(UTC)
    issue_instant = _saml_time(now)
    not_before = _saml_time(now + timedelta(seconds=spec.not_before_offset_seconds))
    not_on_or_after = _saml_time(now + timedelta(seconds=spec.not_on_or_after_offset_seconds))
    scd_offset = (
        spec.scd_not_on_or_after_offset_seconds
        if spec.scd_not_on_or_after_offset_seconds is not None
        else max(spec.not_on_or_after_offset_seconds, 300)
    )
    scd_not_on_or_after = _saml_time(now + timedelta(seconds=scd_offset))

    issuer = spec.issuer_override if spec.issuer_override is not None else spec.idp_entity_id
    audience = spec.audience_override if spec.audience_override is not None else spec.sp_entity_id
    in_response_to = (
        spec.scd_in_response_to_override
        if spec.scd_in_response_to_override is not None
        else (spec.request_id or "")
    )
    in_response_to_attr = f' InResponseTo="{in_response_to}"' if in_response_to else ""

    attr_stmt = _attribute_statement_xml(spec.attributes)

    return f"""<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="{assertion_id}" IssueInstant="{issue_instant}" Version="2.0">
  <saml:Issuer>{issuer}</saml:Issuer>
  <saml:Subject>
    <saml:NameID Format="{spec.subject_nameid_format}">{nameid}</saml:NameID>
    <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
      <saml:SubjectConfirmationData{in_response_to_attr} NotOnOrAfter="{scd_not_on_or_after}" Recipient="{spec.acs_url}"/>
    </saml:SubjectConfirmation>
  </saml:Subject>
  <saml:Conditions NotBefore="{not_before}" NotOnOrAfter="{not_on_or_after}">
    <saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction>
  </saml:Conditions>
  <saml:AuthnStatement AuthnInstant="{issue_instant}" SessionIndex="_{uuid.uuid4().hex}">
    <saml:AuthnContext><saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport</saml:AuthnContextClassRef></saml:AuthnContext>
  </saml:AuthnStatement>
  {attr_stmt}
</saml:Assertion>"""


def build_saml_response_b64(spec: AssertionSpec) -> str:
    """Build a (optionally signed) SAMLResponse and return it base64-encoded,
    ready to POST as the `SAMLResponse` form field."""
    now = datetime.now(UTC)
    issue_instant = _saml_time(now)

    assertion_xml = _build_assertion_xml(
        spec, assertion_id=spec.assertion_id, nameid=spec.subject_nameid
    )

    if spec.sign:
        keypair = spec.sign_with or generate_idp_keypair()
        signed_assertion = OneLogin_Saml2_Utils.add_sign(
            assertion_xml,
            keypair.key_pem,
            keypair.cert_pem,
            sign_algorithm=_SIGN_ALG,
            digest_algorithm=_DIGEST_ALG,
        )
        if isinstance(signed_assertion, bytes):
            signed_assertion = signed_assertion.decode()
        assertion_block = signed_assertion
    else:
        assertion_block = assertion_xml

    if spec.inject_xsw_evil_nameid is not None:
        evil_assertion_id = "_" + uuid.uuid4().hex
        evil_xml = _build_assertion_xml(
            spec, assertion_id=evil_assertion_id, nameid=spec.inject_xsw_evil_nameid
        )
        assertion_block = assertion_block + evil_xml

    response_issuer = (
        spec.issuer_override if spec.issuer_override is not None else spec.idp_entity_id
    )
    response_in_response_to_attr = f' InResponseTo="{spec.request_id}"' if spec.request_id else ""

    response_xml = f"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="{spec.response_id}" Version="2.0" IssueInstant="{issue_instant}" Destination="{spec.acs_url}"{response_in_response_to_attr}>
  <saml:Issuer>{response_issuer}</saml:Issuer>
  <samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>
  {assertion_block}
</samlp:Response>"""

    return base64.b64encode(response_xml.encode()).decode()


def build_signed_response(
    *,
    idp_entity_id: str,
    sp_entity_id: str,
    acs_url: str,
    request_id: str | None,
    keypair: IdpKeyPair,
    subject_email: str = "user@acme.com",
    nameid_format: str = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
    attributes: dict[str, list[str]] | None = None,
    not_before_offset_seconds: int = -60,
    not_on_or_after_offset_seconds: int = 300,
    **overrides: object,
) -> str:
    """Convenience wrapper: build a validly-signed response for the common
    happy-path shape, returning the base64 SAMLResponse."""
    spec = AssertionSpec(
        idp_entity_id=idp_entity_id,
        sp_entity_id=sp_entity_id,
        acs_url=acs_url,
        request_id=request_id,
        subject_nameid=subject_email,
        subject_nameid_format=nameid_format,
        attributes=attributes or {},
        not_before_offset_seconds=not_before_offset_seconds,
        not_on_or_after_offset_seconds=not_on_or_after_offset_seconds,
        sign=True,
        sign_with=keypair,
        **overrides,  # type: ignore[arg-type]
    )
    return build_saml_response_b64(spec)
