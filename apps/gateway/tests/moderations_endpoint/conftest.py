"""Suite-local fixtures for moderations-endpoint tests (PLAN.md §4, DRAFT).

Infrastructure: real Postgres (localhost:5433 gateway_test) + real Redis (localhost:6380 db 9).
The shared root conftest.py provides: settings, app, client, db_session.

Pattern mirrors tests/embeddings_endpoint/conftest.py + tests/audio_endpoints (budget priming).

Key fakes defined here:
  - FakeModerationProvider — records moderate_raw() calls; returns a preset full-wire body,
    or raises UpstreamUnavailableError to simulate a provider outage.
  - SpyRecorder           — counts record() invocations; satisfies UsageRecorder protocol.

Seed helpers:
  - seed_moderation_model — raw SQL insert for models (modality='moderation') + pricing_snapshots
  - seed_chat_model_for_mismatch — an existing-modality model, used for the R:MODEL_MODALITY_MISMATCH case
  - api_key_info          — HTTP admin signup + login + key create; returns dict with key/ids
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.proxy.domain.entities import GuardrailEvent, GuardrailResult
from gateway.proxy.domain.provider_credentials import ProviderKeyMissing

# ---------------------------------------------------------------------------
# Response / payload constants
# ---------------------------------------------------------------------------

MODERATION_MODEL_ID = "omni-moderation-latest"
CHAT_MODEL_ID_FOR_MISMATCH = "openai/gpt-4o-moderation-mismatch-fixture"

_CATEGORY_KEYS = [
    "harassment",
    "harassment/threatening",
    "hate",
    "hate/threatening",
    "illicit",
    "illicit/violent",
    "self-harm",
    "self-harm/instructions",
    "self-harm/intent",
    "sexual",
    "sexual/minors",
    "violence",
    "violence/graphic",
]


def _all_false_categories() -> dict[str, bool]:
    return dict.fromkeys(_CATEGORY_KEYS, False)


def _all_zero_scores() -> dict[str, float]:
    return dict.fromkeys(_CATEGORY_KEYS, 0.0)


def _all_empty_applied_types() -> dict[str, list[str]]:
    return {k: [] for k in _CATEGORY_KEYS}


def make_moderation_response(*, n_results: int = 1, flagged: bool = False) -> dict[str, Any]:
    """Build a full real-wire moderation response body with `n_results` entries."""
    categories = _all_false_categories()
    scores = _all_zero_scores()
    if flagged:
        categories["violence"] = True
        scores["violence"] = 0.86
    result = {
        "flagged": flagged,
        "categories": categories,
        "category_scores": scores,
        "category_applied_input_types": _all_empty_applied_types(),
    }
    return {
        "id": "modr-fixture-0001",
        "model": MODERATION_MODEL_ID,
        "results": [dict(result) for _ in range(n_results)],
    }


MODERATION_RESPONSE_BODY: dict[str, Any] = make_moderation_response(n_results=1)


# ---------------------------------------------------------------------------
# FakeModerationProvider
# ---------------------------------------------------------------------------


class FakeModerationProvider:
    """Stand-in for the widened `OpenAiModerationClient.moderate_raw()` seam.

    Records every call's raw text payload actually sent (so tests can assert PII was
    scrubbed before it reached "upstream"); returns a preset full-wire body, or raises
    to simulate a provider outage (circuit open / timeout / malformed body).
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response: dict[str, Any] = MODERATION_RESPONSE_BODY
        self._raise: Exception | None = None

    def set_response(self, body: dict[str, Any]) -> None:
        self._response = body
        self._raise = None

    def set_raises(self, exc: Exception) -> None:
        self._raise = exc

    async def moderate_raw(self, input_value: Any, *, model: str = MODERATION_MODEL_ID) -> dict[str, Any]:
        self.calls.append({"input": input_value, "model": model})
        if self._raise is not None:
            raise self._raise
        return self._response


# ---------------------------------------------------------------------------
# FailingCredentialResolver -- M5 coverage (mod17)
# ---------------------------------------------------------------------------


class FailingCredentialResolver:
    """TenantCredentialResolver stub whose resolve() always raises ProviderKeyMissing.

    Pattern mirrors tests/guardrails/test_ml_moderation.py::FakeCredentialResolver
    (missing_for=frozenset({"openai"})) but hardcoded to always-missing since this
    suite's only BYOK provider is 'openai'.
    """

    async def resolve(self, tenant_id: object, provider: str) -> Any:
        raise ProviderKeyMissing(provider)


# ---------------------------------------------------------------------------
# AlwaysBlockGuardrailEvaluator -- M9 coverage (mod18)
# ---------------------------------------------------------------------------


class AlwaysBlockGuardrailEvaluator:
    """A guardrail evaluator that unconditionally blocks -- proves /v1/moderations
    never routes its own input through the tenant's guardrail pipeline (M9).

    If the moderations use-case wrongly called evaluate_nonchat_request_guardrails
    over its own input, wiring THIS evaluator via app.state.guardrail_evaluator
    (the same test-injection seam nonchat_guardrail_deps.py::resolve_guardrail_evaluator
    documents: "Reads app.state.guardrail_evaluator first (tests inject failing
    evaluators this way)") would make every request 400 GUARDRAIL_BLOCKED. M9 requires
    a 200 regardless.
    """

    async def evaluate_pre(
        self, messages: list[dict[str, Any]], guardrail_configs: dict[str, Any]
    ) -> GuardrailResult:
        return GuardrailResult(
            blocked=True,
            blocked_by="ml_moderation",
            masked_messages=None,
            events=[GuardrailEvent(guardrail="ml_moderation", action="blocked", detail="test-forced-block")],
        )

    async def evaluate_post(
        self, response_body: dict[str, Any], guardrail_configs: dict[str, Any]
    ) -> dict[str, Any]:
        return response_body


# ---------------------------------------------------------------------------
# SpyRecorder
# ---------------------------------------------------------------------------


class SpyRecorder:
    """Minimal UsageRecorder-compatible spy (mirrors embeddings_endpoint's SpyRecorder)."""

    supported_extras: frozenset[str] = frozenset(
        {
            "team_id",
            "cached",
            "guardrail_blocked",
            "blocked_by",
            "pii_masked",
            "pricing_unit",
            "quantity",
            "usage_source",
        }
    )

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_call(self) -> dict[str, Any]:
        assert self.calls, "SpyRecorder: no record() calls captured"
        return self.calls[-1]

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def seed_moderation_model(
    session: AsyncSession,
    *,
    model_id: str = MODERATION_MODEL_ID,
    active: bool = True,
    modality: str = "moderation",
    provider: str = "openai",
    prompt_usd_per_token: str = "0.0000001",
) -> str:
    """Insert the moderation model row + per_token pricing snapshot into the test DB.

    Mirrors embeddings_endpoint/conftest.py::seed_embedding_model — ON CONFLICT DO
    NOTHING so it's idempotent within a test.
    """
    await session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, modality, provider)"
            " VALUES (:id, :name, NULL, :active, :modality, :provider)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": model_id,
            "name": model_id,
            "active": active,
            "modality": modality,
            "provider": provider,
        },
    )
    snap_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token,"
            "  pricing_unit, unit_usd_per_unit)"
            " VALUES (:id, :mid, :prompt, 0, 'per_token', NULL)"
            " ON CONFLICT DO NOTHING"
        ),
        {"id": snap_id, "mid": model_id, "prompt": prompt_usd_per_token},
    )
    await session.commit()
    return model_id


async def seed_chat_model_for_mismatch(
    session: AsyncSession,
    *,
    model_id: str = CHAT_MODEL_ID_FOR_MISMATCH,
) -> str:
    """Insert a minimal ACTIVE chat model row — used for the modality-mismatch reject case."""
    await session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, modality, provider)"
            " VALUES (:id, :name, 128000, true, 'chat', 'openrouter')"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": model_id, "name": "GPT-4o (mismatch fixture)"},
    )
    await session.commit()
    return model_id


# ---------------------------------------------------------------------------
# api_key_info fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def api_key_info(client: Any) -> dict[str, str]:
    """Signup → login → create key; returns ids + plaintext key.

    Pattern mirrors tests/embeddings_endpoint/conftest.py::api_key_info.
    """
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "ModerationsTest",
            "email": "mod-test@example.io",
            "password": "moderations battery test",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]

    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "mod-test@example.io", "password": "moderations battery test"},
        )
    ).json()["access_token"]

    created = await client.post(
        "/admin/keys",
        json={"name": "mod-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"

    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
        "jwt": token,
    }


# ---------------------------------------------------------------------------
# Convenience: inject a FakeModerationProvider into app.state.ml_moderation_provider
# ---------------------------------------------------------------------------


def inject_fake_moderation_provider(
    app: Any,
    fake_provider: FakeModerationProvider | None = None,
) -> FakeModerationProvider:
    """Mount a FakeModerationProvider as app.state.ml_moderation_provider."""
    if fake_provider is None:
        fake_provider = FakeModerationProvider()
    app.state.ml_moderation_provider = fake_provider
    return fake_provider
