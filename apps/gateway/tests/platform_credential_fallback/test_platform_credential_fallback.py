"""Failing-first (RED) suite for platform-credential-fallback (§4 TESTS plan).

§3 CONTRACT (platform-credential-fallback TASK.md) — FROZEN @ v1.

Behavioral tests on the ONE credential seam
`gateway.proxy.application.use_cases.resolve_provider_credential`, extended additively with a
keyword-only `platform_fallback: PlatformCredentialFallback | None = None`.

TRUE-RED: the seam exists but does NOT yet accept `platform_fallback` (TypeError), and the M8
signal helpers + the kill-switch setting do not exist yet (ImportError / AttributeError). Each
test's RIGHT-REASON is enumerated in its docstring.

One test per Must (M1–M9) and per Reject (R1–R3) in §1/§2.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from gateway.core.errors import ProblemError
from gateway.proxy.application.use_cases import resolve_provider_credential
from gateway.proxy.domain.credential_context import (
    get_credential_tenant,
    get_provider_credential,
    reset_provider_credential,
)

from tests.platform_credential_fallback.conftest import (
    FakePlatformFallback,
    FakeResolver,
    bearer,
)


# ---------------------------------------------------------------------------
# M1 — keyless tenant served by the platform fallback
# ---------------------------------------------------------------------------
async def test_m1_keyless_tenant_served_by_platform_fallback(
    requester_id: uuid.UUID, platform_id: uuid.UUID, platform_secret: str
) -> None:
    """Kill-switch ON, requester has no key, platform HAS a key → platform cred served, no 402.

    RIGHT-REASON RED: resolve_provider_credential does not yet accept `platform_fallback`
    (TypeError: unexpected keyword argument).
    """
    plat_cred = bearer(platform_secret)
    resolver = FakeResolver({(platform_id, "openrouter"): plat_cred})  # requester has NO key
    fb = FakePlatformFallback(enabled=True, platform_id=platform_id)

    token = await resolve_provider_credential(
        resolver, requester_id, "openrouter", platform_fallback=fb
    )
    try:
        assert token is not None, "fallback must serve a credential (not None / not 402)"
        assert get_provider_credential() is plat_cred, "platform credential must be the one served"
    finally:
        reset_provider_credential(token)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# M2 — own key wins; fallback never consulted
# ---------------------------------------------------------------------------
async def test_m2_own_key_precedence(
    requester_id: uuid.UUID,
    platform_id: uuid.UUID,
    own_secret: str,
    platform_secret: str,
) -> None:
    """Requester HAS its own key → own cred served; platform never consulted, no fallback audit.

    RIGHT-REASON RED: `platform_fallback` kwarg not yet accepted (TypeError).
    """
    own_cred = bearer(own_secret)
    resolver = FakeResolver(
        {
            (requester_id, "openrouter"): own_cred,
            (platform_id, "openrouter"): bearer(platform_secret),
        }
    )
    fb = FakePlatformFallback(enabled=True, platform_id=platform_id)

    token = await resolve_provider_credential(
        resolver, requester_id, "openrouter", platform_fallback=fb
    )
    try:
        assert get_provider_credential() is own_cred, "own key must win over platform fallback"
        assert fb.platform_id_calls == 0, "platform must NOT be consulted when own key present"
        assert fb.served_audits == [], "no fallback audit when own key served"
        # resolver consulted only for the requester, never for the platform id
        assert resolver.calls == [(requester_id, "openrouter")]
    finally:
        reset_provider_credential(token)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# M3 — fallback publishes the PLATFORM owner id, not the requester's
# ---------------------------------------------------------------------------
async def test_m3_confused_deputy_owner_is_platform(
    requester_id: uuid.UUID, platform_id: uuid.UUID, platform_secret: str
) -> None:
    """On a served fallback, current_credential_tenant is the PLATFORM id, NOT the requester's.

    RIGHT-REASON RED: `platform_fallback` kwarg not yet accepted (TypeError).
    """
    resolver = FakeResolver({(platform_id, "vertex"): bearer(platform_secret)})
    fb = FakePlatformFallback(enabled=True, platform_id=platform_id)

    token = await resolve_provider_credential(resolver, requester_id, "vertex", platform_fallback=fb)
    try:
        owner = get_credential_tenant()
        assert owner == platform_id, "token-cache owner must be the PLATFORM tenant id"
        assert owner != requester_id, "owner must NOT be the requesting tenant (confused-deputy)"
    finally:
        reset_provider_credential(token)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# M4 — platform credential resolved under the platform key only (cache observable)
# ---------------------------------------------------------------------------
async def test_m4_platform_resolved_under_platform_key(
    requester_id: uuid.UUID, platform_id: uuid.UUID, platform_secret: str
) -> None:
    """The platform lookup uses the PLATFORM tenant id as the resolve key — so the shared
    (tenant_id, provider)-keyed cache stores it under (platform_id, provider), never the requester's.

    RIGHT-REASON RED: `platform_fallback` kwarg not yet accepted (TypeError).
    """
    resolver = FakeResolver({(platform_id, "openrouter"): bearer(platform_secret)})
    fb = FakePlatformFallback(enabled=True, platform_id=platform_id)

    token = await resolve_provider_credential(
        resolver, requester_id, "openrouter", platform_fallback=fb
    )
    try:
        # first call = requester (miss → ProviderKeyMissing), second = platform id
        assert (requester_id, "openrouter") in resolver.calls
        assert (platform_id, "openrouter") in resolver.calls
        # the platform credential was NEVER resolved under the requester's id
        platform_lookups = [c for c in resolver.calls if c == (requester_id, "openrouter")]
        # requester lookup returns a miss; the served cred came from the platform-id lookup
        assert resolver.calls[-1] == (platform_id, "openrouter"), (
            "the served credential must be resolved under the platform id (cache-key correctness)"
        )
        assert len(platform_lookups) == 1  # requester tried exactly once, not re-mapped
    finally:
        reset_provider_credential(token)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# M5 — fallback emits an audit event naming requester + provider
# ---------------------------------------------------------------------------
async def test_m5_fallback_emits_audit(
    requester_id: uuid.UUID, platform_id: uuid.UUID, platform_secret: str
) -> None:
    """A served fallback records an audit naming the requesting tenant + provider.

    RIGHT-REASON RED: `platform_fallback` kwarg not yet accepted (TypeError).
    """
    resolver = FakeResolver({(platform_id, "anthropic"): bearer(platform_secret)})
    fb = FakePlatformFallback(enabled=True, platform_id=platform_id)

    token = await resolve_provider_credential(
        resolver, requester_id, "anthropic", platform_fallback=fb
    )
    try:
        assert fb.served_audits == [(requester_id, "anthropic")], (
            "fallback must audit (requesting tenant, provider)"
        )
        assert fb.misconfig_audits == []
    finally:
        reset_provider_credential(token)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# M6 — kill-switch OFF is byte-identical to today (402, no fallback attempt)
# ---------------------------------------------------------------------------
async def test_m6_kill_switch_off_no_fallback(
    requester_id: uuid.UUID, platform_id: uuid.UUID, platform_secret: str
) -> None:
    """Kill-switch OFF + keyless requester → 402 ERR_PROVIDER_KEY_MISSING; platform never consulted.

    RIGHT-REASON RED: `platform_fallback` kwarg not yet accepted (TypeError).
    """
    resolver = FakeResolver({(platform_id, "openrouter"): bearer(platform_secret)})
    fb = FakePlatformFallback(enabled=False, platform_id=platform_id)  # switch OFF

    with pytest.raises(ProblemError) as exc:
        await resolve_provider_credential(resolver, requester_id, "openrouter", platform_fallback=fb)

    assert exc.value.status == 402
    assert exc.value.code == "ERR_PROVIDER_KEY_MISSING"
    assert fb.platform_id_calls == 0, "switch OFF must not even resolve the platform tenant"
    assert fb.served_audits == []


# ---------------------------------------------------------------------------
# M7 — uniform across providers (the seam is provider-agnostic; all verbs share it)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "provider",
    ["openrouter", "openai", "anthropic", "google", "bedrock", "azure", "minimax", "vertex"],
)
async def test_m7_uniform_across_all_providers(
    provider: str, requester_id: uuid.UUID, platform_id: uuid.UUID, platform_secret: str
) -> None:
    """Every one of the 8 providers falls back through the same seam with no special-casing.

    RIGHT-REASON RED: `platform_fallback` kwarg not yet accepted (TypeError).
    """
    resolver = FakeResolver({(platform_id, provider): bearer(platform_secret)})
    fb = FakePlatformFallback(enabled=True, platform_id=platform_id)

    token = await resolve_provider_credential(resolver, requester_id, provider, platform_fallback=fb)
    try:
        assert token is not None, f"provider {provider} must fall back through the same seam"
        assert fb.served_audits == [(requester_id, provider)]
    finally:
        reset_provider_credential(token)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# M8 — request-scoped "served via platform fallback" signal
# ---------------------------------------------------------------------------
async def test_m8_signal_set_on_fallback_only(
    requester_id: uuid.UUID,
    platform_id: uuid.UUID,
    own_secret: str,
    platform_secret: str,
) -> None:
    """The signal is True while a fallback is bound, False after reset, and never set on own-key.

    RIGHT-REASON RED: served_via_platform_fallback / mark_platform_fallback do not exist yet
    in credential_context (ImportError); and the seam does not accept `platform_fallback`.
    """
    from gateway.proxy.domain.credential_context import (  # type: ignore[import]
        served_via_platform_fallback,
    )

    # (a) fallback path → signal True, then False after reset
    resolver = FakeResolver({(platform_id, "openrouter"): bearer(platform_secret)})
    fb = FakePlatformFallback(enabled=True, platform_id=platform_id)
    token = await resolve_provider_credential(
        resolver, requester_id, "openrouter", platform_fallback=fb
    )
    try:
        assert served_via_platform_fallback() is True, "signal must be set on a served fallback"
    finally:
        reset_provider_credential(token)  # type: ignore[arg-type]
    assert served_via_platform_fallback() is False, "signal must reset with the credential scope"

    # (b) own-key path → signal never set
    own_resolver = FakeResolver({(requester_id, "openrouter"): bearer(own_secret)})
    token2 = await resolve_provider_credential(
        own_resolver, requester_id, "openrouter", platform_fallback=fb
    )
    try:
        assert served_via_platform_fallback() is False, "own-key request must not set the signal"
    finally:
        reset_provider_credential(token2)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# M9 — provider-agnostic: resolution does not validate upstream coverage
# ---------------------------------------------------------------------------
async def test_m9_no_gateway_side_coverage_check(
    requester_id: uuid.UUID, platform_id: uuid.UUID, platform_secret: str
) -> None:
    """A non-bearer (bedrock) fallback resolves at the seam without any coverage validation —
    resolution succeeds and binds the platform credential; upstream (not the seam) owns coverage.

    RIGHT-REASON RED: `platform_fallback` kwarg not yet accepted (TypeError).
    """
    plat_cred = bearer(platform_secret)  # stand-in; the seam is credential-shape agnostic
    resolver = FakeResolver({(platform_id, "bedrock"): plat_cred})
    fb = FakePlatformFallback(enabled=True, platform_id=platform_id)

    token = await resolve_provider_credential(
        resolver, requester_id, "bedrock", platform_fallback=fb
    )
    try:
        # seam does NOT raise a gateway 402 for a "maybe-uncovered" non-bearer provider
        assert token is not None
        assert get_provider_credential() is plat_cred
    finally:
        reset_provider_credential(token)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# R1 — both keys absent → 402
# ---------------------------------------------------------------------------
async def test_r1_both_absent_402(requester_id: uuid.UUID, platform_id: uuid.UUID) -> None:
    """Requester has no key AND platform has no key → 402 ERR_PROVIDER_KEY_MISSING (fail closed).

    RIGHT-REASON RED: `platform_fallback` kwarg not yet accepted (TypeError).
    """
    resolver = FakeResolver({})  # neither requester nor platform has a key
    fb = FakePlatformFallback(enabled=True, platform_id=platform_id)

    with pytest.raises(ProblemError) as exc:
        await resolve_provider_credential(resolver, requester_id, "openrouter", platform_fallback=fb)

    assert exc.value.status == 402
    assert exc.value.code == "ERR_PROVIDER_KEY_MISSING"
    assert get_provider_credential() is None, "no credential bound when both absent"
    # platform WAS consulted (it must try) but found nothing; no served audit
    assert fb.served_audits == []


# ---------------------------------------------------------------------------
# R2 — kill-switch OFF, keyless requester → 402 (distinct: no fallback attempted at all)
# ---------------------------------------------------------------------------
async def test_r2_switch_off_keyless_402(requester_id: uuid.UUID, platform_id: uuid.UUID) -> None:
    """Switch OFF + keyless → 402 and NO fallback machinery is touched.

    RIGHT-REASON RED: `platform_fallback` kwarg not yet accepted (TypeError).
    """
    resolver = FakeResolver({})
    fb = FakePlatformFallback(enabled=False, platform_id=platform_id)

    with pytest.raises(ProblemError) as exc:
        await resolve_provider_credential(resolver, requester_id, "openrouter", platform_fallback=fb)

    assert exc.value.status == 402
    assert exc.value.code == "ERR_PROVIDER_KEY_MISSING"
    assert fb.platform_id_calls == 0
    assert fb.misconfig_audits == []


# ---------------------------------------------------------------------------
# R3 — platform ROW missing while fallback ON → 402 + loud misconfig audit
# ---------------------------------------------------------------------------
async def test_r3_platform_row_missing_402_plus_audit(requester_id: uuid.UUID) -> None:
    """Fallback ON, keyless requester, platform row NOT provisioned (platform_tenant_id → None)
    → 402 to the tenant + an error-level misconfig audit; never a fabricated id / empty cred.

    RIGHT-REASON RED: `platform_fallback` kwarg not yet accepted (TypeError).
    """
    resolver = FakeResolver({})
    fb = FakePlatformFallback(enabled=True, platform_id=None)  # row missing → None

    with pytest.raises(ProblemError) as exc:
        await resolve_provider_credential(resolver, requester_id, "openrouter", platform_fallback=fb)

    assert exc.value.status == 402
    assert exc.value.code == "ERR_PROVIDER_KEY_MISSING"
    assert fb.misconfig_audits == [(requester_id, "openrouter")], (
        "platform-row-missing must emit a loud operator misconfig audit"
    )
    assert fb.served_audits == []
    assert get_provider_credential() is None


# ---------------------------------------------------------------------------
# Kill-switch setting existence (config surface for the wiring)
# ---------------------------------------------------------------------------
def test_kill_switch_setting_defaults_on() -> None:
    """core.config.Settings exposes `platform_credential_fallback_enabled`, default True (default-ON).

    RIGHT-REASON RED: the field does not exist on Settings yet (AttributeError / missing key).
    """
    from gateway.core.config import Settings

    assert "platform_credential_fallback_enabled" in Settings.model_fields, (
        "kill-switch setting must exist on Settings"
    )
    default = Settings.model_fields["platform_credential_fallback_enabled"].default
    assert default is True, "kill-switch must default ON (Tin 2026-07-15)"
