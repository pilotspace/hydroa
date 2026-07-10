"""Red suite for S3 — SSRF/IMDS/credential-exfiltration egress policy
(edge-input-hardening TASK.md §2/§3 FROZEN @ v1).

Pure-unit tests (no real network) over `gateway.core.egress_policy` plus focused
integration tests over the Azure adapters proving: fresh-every-request re-resolution,
DNS-rebinding closure, fail-closed-on-resolver-error, follow_redirects=False (asserted
explicitly), and the AllowAllEgressPolicy test seam.

RED reason (pre-BUILD): `gateway.core.egress_policy` does not exist -> ImportError.
"""

from __future__ import annotations

import httpx
import pytest

from gateway.core.egress_policy import (
    AllowAllEgressPolicy,
    DenyPrivateAndMetadataEgressPolicy,
    EgressDeniedError,
    assert_literal_host_not_denied,
)
from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.provider_credentials import AzureCredential
from gateway.proxy.infrastructure.azure_ad import AzureADTokenProvider
from gateway.proxy.infrastructure.azure_embeddings import AzureEmbeddingsProvider
from gateway.proxy.infrastructure.azure_upstream import AzureCompletionUpstream


class _FakeResolver:
    """Injectable DnsResolver — returns a fixed answer, or raises to simulate failure."""

    def __init__(
        self, answers: dict[str, list[str]] | None = None, *, raise_error: bool = False
    ) -> None:
        self._answers = answers or {}
        self._raise_error = raise_error
        self.calls: list[str] = []

    async def resolve(self, host: str) -> list[str]:
        self.calls.append(host)
        if self._raise_error:
            raise OSError("simulated DNS failure")
        return self._answers.get(host, [])


class _HangingResolver:
    """Injectable DnsResolver that never returns — exercises the resolve-timeout path."""

    async def resolve(self, host: str) -> list[str]:
        import asyncio

        await asyncio.sleep(999)
        return []


# ---------------------------------------------------------------------------
# Write-time literal check (assert_literal_host_not_denied) — sync, no DNS
# ---------------------------------------------------------------------------


def test_write_time_metadata_ip_denied() -> None:
    with pytest.raises(EgressDeniedError):
        assert_literal_host_not_denied("http://169.254.169.254/latest/meta-data/")


def test_write_time_metadata_ip_denied_even_with_private_ranges_allowed() -> None:
    """Metadata addresses are NEVER toggle-able — even with allow_private_ranges=True."""
    with pytest.raises(EgressDeniedError):
        assert_literal_host_not_denied("https://169.254.169.254/", allow_private_ranges=True)


def test_write_time_rfc1918_literal_denied_by_default() -> None:
    with pytest.raises(EgressDeniedError):
        assert_literal_host_not_denied("https://10.0.0.5/openai")


def test_write_time_rfc1918_literal_allowed_with_opt_in() -> None:
    assert_literal_host_not_denied("https://10.20.30.40/openai", allow_private_ranges=True)


def test_write_time_hostname_always_passes_no_dns_performed() -> None:
    """A non-IP-literal hostname (the common real-world case) always passes this check —
    DNS resolution is deliberately deferred to the request-time check."""
    assert_literal_host_not_denied("https://attacker.example.com/")
    assert_literal_host_not_denied("https://my-resource.openai.azure.com/")


def test_write_time_non_https_scheme_denied_without_dev_opt_in() -> None:
    with pytest.raises(EgressDeniedError):
        assert_literal_host_not_denied("http://my-resource.openai.azure.com/")


def test_write_time_http_allowed_with_dev_opt_in() -> None:
    assert_literal_host_not_denied("http://my-resource.openai.azure.com/", allow_http=True)


# ---------------------------------------------------------------------------
# Request-time DenyPrivateAndMetadataEgressPolicy — async, injected fake resolver
# ---------------------------------------------------------------------------


async def test_request_time_denies_metadata_and_private_by_default() -> None:
    policy = DenyPrivateAndMetadataEgressPolicy()
    for ip in ("127.0.0.1", "169.254.169.254", "fd00:ec2::254", "10.0.0.5"):
        resolver = _FakeResolver({"example.com": [ip]})
        policy = DenyPrivateAndMetadataEgressPolicy(resolver=resolver)
        with pytest.raises(EgressDeniedError):
            await policy.check("https://example.com/x")


async def test_request_time_dns_rebind_denied_fresh_every_call() -> None:
    """A hostname that resolved to a public IP on an earlier call now resolves to the
    metadata address — the check re-resolves FRESH every time (never cached) and denies."""
    # 93.184.216.34 (example.com's real, ordinary public IP) — genuinely public, unlike the
    # TEST-NET-3 range (203.0.113.0/24), which Python's ipaddress.is_private ALSO treats as
    # a documentation/reserved (denied) range.
    resolver = _FakeResolver({"rebind.example.com": ["93.184.216.34"]})
    policy = DenyPrivateAndMetadataEgressPolicy(resolver=resolver)
    await policy.check("https://rebind.example.com/x")  # public IP — allowed

    # DNS TTL "expires"; the authoritative answer changes to the metadata address.
    resolver._answers["rebind.example.com"] = ["169.254.169.254"]
    with pytest.raises(EgressDeniedError):
        await policy.check("https://rebind.example.com/x")
    assert resolver.calls.count("rebind.example.com") == 2, (
        "each check() call must trigger its OWN fresh resolve — never cached"
    )


async def test_request_time_private_range_allowed_with_opt_in_metadata_still_denied() -> None:
    resolver = _FakeResolver({"privatelink.example.com": ["10.20.30.40"]})
    policy = DenyPrivateAndMetadataEgressPolicy(allow_private_ranges=True, resolver=resolver)
    await policy.check("https://privatelink.example.com/x")  # allowed under opt-in

    resolver2 = _FakeResolver({"imds.example.com": ["169.254.169.254"]})
    policy2 = DenyPrivateAndMetadataEgressPolicy(allow_private_ranges=True, resolver=resolver2)
    with pytest.raises(EgressDeniedError):
        await policy2.check("https://imds.example.com/x")  # metadata STILL denied


async def test_request_time_resolver_error_fails_closed() -> None:
    resolver = _FakeResolver(raise_error=True)
    policy = DenyPrivateAndMetadataEgressPolicy(resolver=resolver)
    with pytest.raises(EgressDeniedError):
        await policy.check("https://nxdomain.example.com/x")


async def test_request_time_resolver_timeout_fails_closed() -> None:
    policy = DenyPrivateAndMetadataEgressPolicy(resolver=_HangingResolver(), resolve_timeout_s=0.05)
    with pytest.raises(EgressDeniedError):
        await policy.check("https://slow.example.com/x")


async def test_request_time_public_hostname_allowed() -> None:
    resolver = _FakeResolver({"api.openai.azure.com": ["20.1.2.3"]})
    policy = DenyPrivateAndMetadataEgressPolicy(resolver=resolver)
    await policy.check("https://api.openai.azure.com/x")  # does not raise


async def test_request_time_non_https_scheme_denied() -> None:
    policy = DenyPrivateAndMetadataEgressPolicy()
    with pytest.raises(EgressDeniedError):
        await policy.check("ftp://example.com/x")


# ---------------------------------------------------------------------------
# AllowAllEgressPolicy — the explicit test-only seam
# ---------------------------------------------------------------------------


async def test_allow_all_egress_policy_never_denies() -> None:
    policy = AllowAllEgressPolicy()
    await policy.check("http://169.254.169.254/")  # does not raise — TEST-ONLY fake


# ---------------------------------------------------------------------------
# follow_redirects=False asserted explicitly on every BYOK-influenced httpx client
# ---------------------------------------------------------------------------


def test_azure_completion_upstream_client_never_follows_redirects() -> None:
    upstream = AzureCompletionUpstream(token_provider_cache=None)
    assert upstream._client.follow_redirects is False  # noqa: SLF001


def test_azure_openai_provider_client_never_follows_redirects() -> None:
    provider = AzureEmbeddingsProvider(token_provider_cache=None)
    assert provider._client.follow_redirects is False  # noqa: SLF001


def test_azure_ad_token_provider_client_never_follows_redirects() -> None:
    from gateway.proxy.infrastructure.azure_ad import AzureADConfig

    tp = AzureADTokenProvider(
        config=AzureADConfig(tenant_id="t", client_id="c", client_secret="s")  # noqa: S106
    )
    assert tp._client.follow_redirects is False  # noqa: SLF001


# ---------------------------------------------------------------------------
# A 3xx from a BYOK-influenced endpoint is not followed toward IMDS
# ---------------------------------------------------------------------------


async def test_redirect_response_is_not_followed_and_passed_through() -> None:
    """The stub returns a bare 302 with a metadata Location header; since
    follow_redirects=False, httpx must NOT dial the Location target — exactly one request
    is observed, and the 302 is returned/mapped as an ordinary upstream response."""
    call_count = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        assert request.url.host != "169.254.169.254", (
            "the redirect target must NEVER be dialed — follow_redirects=False"
        )
        return httpx.Response(
            302,
            headers={"Location": "http://169.254.169.254/latest/meta-data/"},
            json={},
        )

    upstream = AzureCompletionUpstream(
        token_provider_cache=None, egress_policy=AllowAllEgressPolicy()
    )
    upstream._client = httpx.AsyncClient(  # noqa: SLF001
        transport=httpx.MockTransport(_handler), follow_redirects=False
    )

    cred = AzureCredential(
        mode="api_key",
        endpoint="https://my-resource.openai.azure.com",
        api_key="k",  # noqa: S106
    )
    tok = set_provider_credential(cred)
    try:
        status, _body = await upstream.complete(
            {"model": "az-chat", "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(tok)

    assert status == 302
    assert call_count == 1, "exactly one request — the redirect target was never dialed"


# ---------------------------------------------------------------------------
# The tenant's secret is never attached to a request that egress denies
# ---------------------------------------------------------------------------


async def test_denied_dial_never_carries_the_tenant_secret() -> None:
    """The egress check runs BEFORE _auth_headers_for_credential — a request to a URL the
    policy denies must never even reach the point where the api-key header is attached
    (proven here by the transport never being invoked at all)."""

    class _DenyAllPolicy:
        async def check(self, url: str) -> None:
            raise EgressDeniedError("test_deny_all")

    transport_calls = 0

    def _handler(_request: httpx.Request) -> httpx.Response:
        nonlocal transport_calls
        transport_calls += 1
        return httpx.Response(200, json={})

    upstream = AzureCompletionUpstream(token_provider_cache=None, egress_policy=_DenyAllPolicy())
    upstream._client = httpx.AsyncClient(  # noqa: SLF001
        transport=httpx.MockTransport(_handler), follow_redirects=False
    )

    cred = AzureCredential(
        mode="api_key",
        endpoint="https://my-resource.openai.azure.com",
        api_key="super-secret-never-sent",  # noqa: S106
    )
    tok = set_provider_credential(cred)
    try:
        with pytest.raises(Exception) as exc_info:  # ProblemError (UPSTREAM_EGRESS_DENIED)
            await upstream.complete(
                {"model": "az-chat", "messages": [{"role": "user", "content": "hi"}]}
            )
    finally:
        reset_provider_credential(tok)

    assert getattr(exc_info.value, "code", None) == "ERR_UPSTREAM_EGRESS_DENIED"
    assert transport_calls == 0, "the denied dial must never reach the HTTP transport"
