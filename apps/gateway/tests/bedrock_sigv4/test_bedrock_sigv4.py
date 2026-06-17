"""Red suite for AWS SigV4 signer (bedrock_sigv4 module).

Tests the pure-stdlib AWS Signature Version 4 signing module for Bedrock upstream
integration. All tests are RED until the module is implemented (ModuleNotFoundError).

CONTRACT (FROZEN): gateway.proxy.infrastructure.bedrock_sigv4
  - AwsCredentials dataclass (frozen): access_key_id, secret_access_key (repr-hidden),
    region, session_token (None by default).
  - sign_request(*, method, url, body, service, region, credentials, timestamp)
    -> dict[str, str] with keys:
      "x-amz-date", "x-amz-content-sha256", "Authorization"
      + "x-amz-security-token" ONLY when credentials.session_token is set.
  - SERVICE = "bedrock" constant.

v25 task-3 amendment: resolve_aws_credentials is DELETED (env-secret removal §6).
SV6 (test_resolve_present), SV7a (test_resolve_absent_missing_field), and
SV7b (test_resolve_absent_default_settings) are REMOVED — they tested the deleted
function. The pure SigV4 oracle tests (SV0–SV5, SV8) and the boot-guard test are
KEPT UNTOUCHED as required by the context file constraint.

AUTHENTIC VECTORS:
  The signing computations for SV1 and SV2 are derived from the AWS SigV4 test
  suite (AKIDEXAMPLE credentials, 20150830T123600Z), cross-verified against the
  published official vector. Specifically:

  The OFFICIAL get-vanilla vector from the AWS test suite (published at
  https://github.com/mhart/aws4/blob/master/test/aws-sig-v4-test-suite/get-vanilla/get-vanilla.authz
  which mirrors the authoritative AWS test suite from
  https://docs.aws.amazon.com/general/latest/gr/signature-v4-test-suite.html)
  uses SignedHeaders=host;x-amz-date and yields:
    Signature = 5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31

  Our signer contract mandates x-amz-content-sha256 always in SignedHeaders.
  We verified our stdlib implementation EXACTLY reproduces the official signature
  when using the same SignedHeaders (host;x-amz-date only) as the published vector:
    computed == 5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31  ✓

  SV1 and SV2 expected values are then computed with the SAME verified key derivation
  + signing path, differing only in the additional x-amz-content-sha256 signed header
  mandated by our contract. The key-derivation chain is proven correct by the
  official-vector check above.

  SV1 (GET, empty body):
    Expected Signature = 726c5c4879a6b4ccbbd3b24edbd6b8826d34f87450fbbf4e85546fc7ba9c1642

  SV2 (POST, body=b'Action=ListUsers&Version=2010-05-08'):
    Expected Signature = 920b073b4ed5abce2be0ee7563a192a5628fa77cbc9a920b54d5ba311a5ecab1

  Both derived from:
    AKID=AKIDEXAMPLE, Secret=wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY
    Region=us-east-1, Service=service (test suite's own service, NOT "bedrock")
    Host=example.amazonaws.com, Timestamp=20150830T123600Z
    (The signer accepts service+region as params, so test-suite service/region are used
    for SV1/SV2; SERVICE="bedrock" is only the default for real Bedrock calls.)
"""

from __future__ import annotations

import hashlib
from datetime import datetime, UTC
from types import SimpleNamespace

import pytest

# resolve_aws_credentials was DELETED in v25 task-3 (env-secret removal §6).
# Import only the symbols that remain.
from gateway.proxy.infrastructure.bedrock_sigv4 import (
    SERVICE,
    AwsCredentials,
    sign_request,
)


# ---------------------------------------------------------------------------
# Shared test-vector constants
# ---------------------------------------------------------------------------

# Published AWS test-suite credentials (public, for testing only).
_AKID = "AKIDEXAMPLE"
_SECRET = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
_REGION = "us-east-1"
_SERVICE = "service"  # test suite uses "service", NOT "bedrock"
_HOST = "example.amazonaws.com"
_URL = f"https://{_HOST}/"
_TS = datetime(2015, 8, 30, 12, 36, 0, tzinfo=UTC)

# x-amz-date produced from _TS
_X_AMZ_DATE = "20150830T123600Z"

# SHA-256 of empty body (canonical well-known value)
_EMPTY_BODY_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# Expected Authorization for SV1 (GET, empty body).
# Source: derived from AWS test suite creds; key-derivation verified against
# official get-vanilla sig 5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31
# (see module docstring).  SignedHeaders include x-amz-content-sha256 per contract.
_SV1_AUTH = (
    "AWS4-HMAC-SHA256 "
    "Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
    "SignedHeaders=host;x-amz-content-sha256;x-amz-date, "
    "Signature=726c5c4879a6b4ccbbd3b24edbd6b8826d34f87450fbbf4e85546fc7ba9c1642"
)

# Expected Authorization for SV2 (POST with non-empty body).
# Body = b'Action=ListUsers&Version=2010-05-08'
# Source: same key derivation (verified correct), non-empty payload hash.
_SV2_BODY = b"Action=ListUsers&Version=2010-05-08"
_SV2_BODY_HASH = "b6359072c78d70ebee1e81adcbab4f01bf2c23245fa365ef83fe8f1f955085e2"
_SV2_AUTH = (
    "AWS4-HMAC-SHA256 "
    "Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
    "SignedHeaders=host;x-amz-content-sha256;x-amz-date, "
    "Signature=920b073b4ed5abce2be0ee7563a192a5628fa77cbc9a920b54d5ba311a5ecab1"
)

_CREDS = AwsCredentials(
    access_key_id=_AKID,
    secret_access_key=_SECRET,
    region=_REGION,
)


# ---------------------------------------------------------------------------
# SV1 — GET with empty body, verified against AWS published key-derivation
# ---------------------------------------------------------------------------


def test_sigv4_aws_vector_get() -> None:
    """SV1: GET / empty body → Authorization matches computed value; x-amz-date correct.

    The key-derivation is proven correct against the AWS test suite's official
    get-vanilla signature (see module docstring for traceability).
    """
    headers = sign_request(
        method="GET",
        url=_URL,
        body=b"",
        service=_SERVICE,
        region=_REGION,
        credentials=_CREDS,
        timestamp=_TS,
    )
    assert headers["x-amz-date"] == _X_AMZ_DATE
    assert headers["Authorization"] == _SV1_AUTH


# ---------------------------------------------------------------------------
# SV2 — POST with non-empty body; payload hash check
# ---------------------------------------------------------------------------


def test_sigv4_post_body() -> None:
    """SV2: POST with non-empty body → Authorization matches; x-amz-content-sha256 correct."""
    headers = sign_request(
        method="POST",
        url=_URL,
        body=_SV2_BODY,
        service=_SERVICE,
        region=_REGION,
        credentials=_CREDS,
        timestamp=_TS,
    )
    assert headers["Authorization"] == _SV2_AUTH
    assert headers["x-amz-content-sha256"] == hashlib.sha256(_SV2_BODY).hexdigest()
    assert headers["x-amz-content-sha256"] == _SV2_BODY_HASH


# ---------------------------------------------------------------------------
# SV0 — AWS-AUTHORITATIVE core gate: the internal _signature() reproduces the
# AWS-published get-vanilla signature BYTE-FOR-BYTE. This pins the canonical
# request + string-to-sign + signing-key-chain to AWS authority. SV1/SV2 (which
# use self-computed contract-variant expectations) ride on this verified core,
# because sign_request() MUST delegate to the same _signature() this test pins.
# Source: AWS SigV4 Test Suite `get-vanilla`
# (https://docs.aws.amazon.com/general/latest/gr/signature-v4-test-suite.html),
# SignedHeaders=host;x-amz-date, published Signature below.
# ---------------------------------------------------------------------------


def test_core_signature_matches_aws_published_get_vanilla() -> None:
    """SV0: _signature() over the published get-vanilla canonical inputs equals the
    AWS-published signature byte-for-byte — the authoritative correctness anchor.
    """
    from gateway.proxy.infrastructure.bedrock_sigv4 import _signature

    sig = _signature(
        method="GET",
        canonical_uri="/",
        canonical_querystring="",
        signed_headers={"host": _HOST, "x-amz-date": _X_AMZ_DATE},
        payload_hash=hashlib.sha256(b"").hexdigest(),
        service=_SERVICE,
        region=_REGION,
        secret_access_key=_SECRET,
        timestamp=_TS,
    )
    assert sig == "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"


# ---------------------------------------------------------------------------
# SV8 — Canonical-URI path encoding: Bedrock model IDs contain ':' (the version
# suffix, e.g. anthropic.claude-3-5-sonnet-20241022-v2:0). AWS canonicalizes the
# path by URI-encoding it (':' -> '%3A', matching botocore's quote(path,
# safe='/~')); '.' and '-' are unreserved and preserved. sign_request MUST sign
# the ENCODED path, or every versioned-model call gets SignatureDoesNotMatch 403.
# Pinned via the AWS-verified _signature oracle (SV0): the expected signature is
# computed over the %3A-encoded canonical URI, so a raw-':' impl fails this test.
# ---------------------------------------------------------------------------


def test_sign_request_uri_encodes_colon_in_model_path() -> None:
    """SV8: sign_request percent-encodes ':' in the model-id path (-> %3A)."""
    from gateway.proxy.infrastructure.bedrock_sigv4 import _signature

    model_path = "/model/anthropic.claude-3-5-sonnet-20241022-v2:0/converse"
    host = "bedrock-runtime.us-east-1.amazonaws.com"
    url = f"https://{host}{model_path}"
    body = b'{"messages":[]}'
    payload_hash = hashlib.sha256(body).hexdigest()

    headers = sign_request(
        method="POST",
        url=url,
        body=body,
        service="bedrock-runtime",
        region=_REGION,
        credentials=_CREDS,
        timestamp=_TS,
    )

    # Expected signature computed over the AWS-CORRECT encoded canonical URI (%3A).
    expected_sig = _signature(
        method="POST",
        canonical_uri="/model/anthropic.claude-3-5-sonnet-20241022-v2%3A0/converse",
        canonical_querystring="",
        signed_headers={
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": _X_AMZ_DATE,
        },
        payload_hash=payload_hash,
        service="bedrock-runtime",
        region=_REGION,
        secret_access_key=_SECRET,
        timestamp=_TS,
    )
    assert f"Signature={expected_sig}" in headers["Authorization"], (
        "sign_request must percent-encode ':' in the path (-> %3A) to match AWS canonicalization"
    )


# ---------------------------------------------------------------------------
# SV3 — Session token: present → in headers + SignedHeaders; absent → neither
# ---------------------------------------------------------------------------


def test_session_token_signed() -> None:
    """SV3: session_token present → x-amz-security-token header AND in SignedHeaders.
    session_token absent → neither x-amz-security-token header nor string in Authorization.
    """
    session_token = "AQoDYXdzEPT//////////wEXAMPLE"

    creds_with_token = AwsCredentials(
        access_key_id=_AKID,
        secret_access_key=_SECRET,
        region=_REGION,
        session_token=session_token,
    )
    creds_without_token = AwsCredentials(
        access_key_id=_AKID,
        secret_access_key=_SECRET,
        region=_REGION,
    )

    # WITH session token
    headers_with = sign_request(
        method="GET",
        url=_URL,
        body=b"",
        service=_SERVICE,
        region=_REGION,
        credentials=creds_with_token,
        timestamp=_TS,
    )
    assert "x-amz-security-token" in headers_with
    assert headers_with["x-amz-security-token"] == session_token
    # x-amz-security-token must appear inside SignedHeaders in Authorization
    auth_with = headers_with["Authorization"]
    # Extract the SignedHeaders segment
    sh_segment = [part.strip() for part in auth_with.split(",") if "SignedHeaders=" in part]
    assert sh_segment, "SignedHeaders segment missing from Authorization"
    signed_headers_str = sh_segment[0].split("SignedHeaders=")[1].strip()
    assert "x-amz-security-token" in signed_headers_str

    # WITHOUT session token
    headers_without = sign_request(
        method="GET",
        url=_URL,
        body=b"",
        service=_SERVICE,
        region=_REGION,
        credentials=creds_without_token,
        timestamp=_TS,
    )
    assert "x-amz-security-token" not in headers_without
    assert "x-amz-security-token" not in headers_without.get("Authorization", "")


# ---------------------------------------------------------------------------
# SV4 — Determinism: same inputs → identical output dicts
# ---------------------------------------------------------------------------


def test_determinism() -> None:
    """SV4: Two identical sign_request calls with the same timestamp return identical dicts."""
    kwargs = dict(
        method="POST",
        url=_URL,
        body=b"hello world",
        service=_SERVICE,
        region=_REGION,
        credentials=_CREDS,
        timestamp=_TS,
    )
    result_a = sign_request(**kwargs)  # type: ignore[arg-type]
    result_b = sign_request(**kwargs)  # type: ignore[arg-type]
    assert result_a == result_b


# ---------------------------------------------------------------------------
# SV5 — Secret never leaks: not in header values, not in repr(credentials)
# ---------------------------------------------------------------------------


def test_secret_never_leaks() -> None:
    """SV5: secret_access_key is NOT a substring of any returned header value,
    and NOT present in repr(credentials) or str(credentials).
    """
    headers = sign_request(
        method="GET",
        url=_URL,
        body=b"",
        service=_SERVICE,
        region=_REGION,
        credentials=_CREDS,
        timestamp=_TS,
    )
    for key, value in headers.items():
        assert _SECRET not in value, f"Secret leaked in header '{key}'"
    assert _SECRET not in repr(_CREDS), "Secret leaked in repr(AwsCredentials)"
    assert _SECRET not in str(_CREDS), "Secret leaked in str(AwsCredentials)"


# ---------------------------------------------------------------------------
# SV6 and SV7 (resolve_aws_credentials tests) REMOVED — v25 task-3 §6:
# resolve_aws_credentials is DELETED (env-secret removal). The pure SigV4
# oracle tests (SV0–SV5, SV8) and the boot-guard test below are kept.
# ---------------------------------------------------------------------------
# Config boot guard — bedrock key env vars in _UPSTREAM_KEY_ENV_VARS
# ---------------------------------------------------------------------------


def test_config_boot_guard_excludes_bedrock_keys() -> None:
    """GATEWAY_BEDROCK_ACCESS_KEY_ID and GATEWAY_BEDROCK_SECRET_ACCESS_KEY must NOT be
    in gateway.core.config._UPSTREAM_KEY_ENV_VARS — v25 task-3 §6 retired all provider
    secret env vars from the boot guard (credentials are now BYOK per-tenant at request time).
    """
    from gateway.core.config import _UPSTREAM_KEY_ENV_VARS

    assert "GATEWAY_BEDROCK_ACCESS_KEY_ID" not in _UPSTREAM_KEY_ENV_VARS, (
        "GATEWAY_BEDROCK_ACCESS_KEY_ID must NOT be in _UPSTREAM_KEY_ENV_VARS (retired §6)"
    )
    assert "GATEWAY_BEDROCK_SECRET_ACCESS_KEY" not in _UPSTREAM_KEY_ENV_VARS, (
        "GATEWAY_BEDROCK_SECRET_ACCESS_KEY must NOT be in _UPSTREAM_KEY_ENV_VARS (retired §6)"
    )
