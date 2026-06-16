"""Pure-stdlib AWS Signature Version 4 signer for Bedrock upstream calls.

stdlib ONLY — no boto3 / botocore / awscrt dependency.

Public surface (frozen contract):
  - AwsCredentials      dataclass (frozen); secret_access_key excluded from repr
  - SERVICE             = "bedrock"  (default service name for real Bedrock calls)
  - sign_request(*, method, url, body, service, region, credentials, timestamp)
      -> dict[str, str]

Internal:
  - _signature(...)     core SigV4 computation; exposed so SV0 test can pin it
                        to the AWS-published get-vanilla vector byte-for-byte.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote, urlparse

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SERVICE: str = "bedrock"  # default service name for real Bedrock-runtime calls


# ---------------------------------------------------------------------------
# AwsCredentials
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AwsCredentials:
    """Immutable AWS credential bundle.

    ``secret_access_key`` is excluded from ``repr``/``str`` so it never
    appears in logs, exception messages, or debug output.

    Field ordering: access_key_id, secret_access_key, region are all
    required (no defaults). session_token is optional (default None).
    Using ``field(repr=False)`` on secret_access_key without a default
    is valid — the field remains required positionally.
    """

    access_key_id: str
    secret_access_key: str = field(repr=False)
    region: str
    session_token: str | None = None


# ---------------------------------------------------------------------------
# _hmac_sha256  (internal helper)
# ---------------------------------------------------------------------------


def _hmac_sha256(key: bytes, data: str) -> bytes:
    """Return HMAC-SHA256(key, data.encode('utf-8'))."""
    return hmac.new(key, data.encode("utf-8"), hashlib.sha256).digest()


# ---------------------------------------------------------------------------
# _signature  (core SigV4 computation — shared by sign_request + SV0 test)
# ---------------------------------------------------------------------------


def _signature(
    *,
    method: str,
    canonical_uri: str,
    canonical_querystring: str,
    signed_headers: dict[str, str],
    payload_hash: str,
    service: str,
    region: str,
    secret_access_key: str,
    timestamp: datetime,
) -> str:
    """Compute the SigV4 signature and return it as a lowercase hex string.

    ``signed_headers`` is ``{lowercase-name: value}`` — exactly the headers
    the caller wants signed.  The function sorts them internally before
    building the canonical form.

    This is the EXACT algorithm from the AWS SigV4 specification:
      https://docs.aws.amazon.com/general/latest/gr/signature-v4-create-canonical-request.html

    SV0 pins this function against the AWS-published get-vanilla vector:
      Signature = 5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31
    """
    # ── Timestamps ──────────────────────────────────────────────────────────
    ts_utc = timestamp.astimezone(UTC) if timestamp.tzinfo is not None else timestamp
    amzdate = ts_utc.strftime("%Y%m%dT%H%M%SZ")
    datestamp = ts_utc.strftime("%Y%m%d")

    # ── Canonical headers ────────────────────────────────────────────────────
    # Each line: lowercased-name + ":" + stripped-value + "\n"
    sorted_keys = sorted(signed_headers)
    canonical_headers = "".join(f"{k}:{signed_headers[k].strip()}\n" for k in sorted_keys)
    signed_headers_list = ";".join(sorted_keys)

    # ── Canonical request ────────────────────────────────────────────────────
    canonical_request = "\n".join(
        [
            method,
            canonical_uri,
            canonical_querystring,
            canonical_headers,
            signed_headers_list,
            payload_hash,
        ]
    )

    # ── Credential scope + string-to-sign ───────────────────────────────────
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    cr_hash = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = f"AWS4-HMAC-SHA256\n{amzdate}\n{scope}\n{cr_hash}"

    # ── Signing key derivation ───────────────────────────────────────────────
    k_date = _hmac_sha256(("AWS4" + secret_access_key).encode("utf-8"), datestamp)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, service)
    k_signing = _hmac_sha256(k_service, "aws4_request")

    # ── Final signature ──────────────────────────────────────────────────────
    return hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# sign_request  (public API)
# ---------------------------------------------------------------------------


def sign_request(
    *,
    method: str,
    url: str,
    body: bytes,
    service: str,
    region: str,
    credentials: AwsCredentials,
    timestamp: datetime,
) -> dict[str, str]:
    """Sign an HTTP request using AWS Signature Version 4.

    Returns a ``dict[str, str]`` containing the headers to ADD to the request:
      - ``x-amz-date``
      - ``x-amz-content-sha256``
      - ``Authorization``
      - ``x-amz-security-token``  (only when ``credentials.session_token`` is set)

    The secret is consumed ONLY inside ``_signature``; it never appears in the
    returned dict.

    Args:
        method:      HTTP method in upper-case (e.g. ``"GET"``, ``"POST"``).
        url:         Full URL including scheme, host, path, and optional query.
        body:        Raw request body bytes (``b""`` for requests without a body).
        service:     AWS service name for signing (e.g. ``"bedrock-runtime"``).
        region:      AWS region (e.g. ``"us-east-1"``).
        credentials: AWS credential bundle.
        timestamp:   Fixed signing timestamp (deterministic; caller controls it).

    Returns:
        Headers dict to merge into the outgoing request.
    """
    # ── Parse URL ────────────────────────────────────────────────────────────
    parsed = urlparse(url)
    host = parsed.hostname or ""
    # Preserve non-standard ports in the Host header (AWS requirement).
    if parsed.port and parsed.port not in (80, 443):
        host = f"{host}:{parsed.port}"

    # Canonical URI: the path component, AWS-URI-encoded ("/" for root).
    # Bedrock model IDs carry a ':' version suffix (e.g. ...-v2:0) which AWS
    # canonicalizes to %3A; mirror botocore's quote(path, safe="/~") so the
    # signed path matches what AWS reconstructs (else SignatureDoesNotMatch).
    canonical_uri = quote(parsed.path or "/", safe="/~")

    # Canonical query string: pre-encoded query from the URL (empty when absent).
    canonical_querystring = parsed.query or ""

    # ── Payload hash ─────────────────────────────────────────────────────────
    payload_hash = hashlib.sha256(body).hexdigest()

    # ── Timestamps ───────────────────────────────────────────────────────────
    ts_utc = timestamp.astimezone(UTC) if timestamp.tzinfo is not None else timestamp
    amzdate = ts_utc.strftime("%Y%m%dT%H%M%SZ")
    datestamp = ts_utc.strftime("%Y%m%d")

    # ── Build signed-header set ───────────────────────────────────────────────
    signed_headers: dict[str, str] = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amzdate,
    }
    if credentials.session_token:
        signed_headers["x-amz-security-token"] = credentials.session_token

    # ── Compute signature (delegates to shared core) ─────────────────────────
    sig = _signature(
        method=method,
        canonical_uri=canonical_uri,
        canonical_querystring=canonical_querystring,
        signed_headers=signed_headers,
        payload_hash=payload_hash,
        service=service,
        region=region,
        secret_access_key=credentials.secret_access_key,
        timestamp=timestamp,
    )

    # ── Build credential scope + SignedHeaders list ──────────────────────────
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    signed_headers_list = ";".join(sorted(signed_headers))
    authorization = (
        f"AWS4-HMAC-SHA256 "
        f"Credential={credentials.access_key_id}/{scope}, "
        f"SignedHeaders={signed_headers_list}, "
        f"Signature={sig}"
    )

    # ── Assemble return dict ─────────────────────────────────────────────────
    result: dict[str, str] = {
        "x-amz-date": amzdate,
        "x-amz-content-sha256": payload_hash,
        "Authorization": authorization,
    }
    if credentials.session_token:
        result["x-amz-security-token"] = credentials.session_token

    return result
