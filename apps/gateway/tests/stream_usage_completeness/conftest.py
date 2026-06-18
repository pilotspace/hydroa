"""Suite-local fixtures for stream-usage-completeness tests (TASK.md §4).

Reuses the streaming_resilience harness (fast, no DB/Redis/server) to drive
CompletionUseCase.stream directly with a plan-driven fake upstream, plus a
marker-aware spy recorder so the new `usage_source` extra survives the typed
UsageRecordExtras capability filter (a recorder must DECLARE supported_extras
to receive it — the streaming_resilience FakeUsageRecorder does not).

The DB test (SU6) uses the real RecordingUsageRecorder + UsageLedgerFlusher
against Postgres (localhost:5433) + a fake Redis stream, mirroring the t2
provider_cost_reconciliation DB persistence pattern.
"""

from __future__ import annotations

from typing import Any

import pytest

# Proven streaming_resilience fakes (module-level, importable).
from tests.streaming_resilience.conftest import (
    A0,
    ALIAS,
    CAND_A,
    CAND_B,
    DONE,
    FakeAuthenticator,
    FakeModelChecker,
    PlanStreamUpstream,
    make_payload,
)

from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.application.use_cases import CompletionUseCase

# --- SSE usage frames -------------------------------------------------------
# A COMPLETE terminal frame (positive top-level counts).
COMPLETE_USAGE = b'data: {"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n'
# A PARTIAL frame: a usage dict with NO positive token count.
PARTIAL_USAGE = b'data: {"usage":{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}}\n\n'
# A COMPLETE frame carrying the t1 tiered tiers (must survive the tee).
TIERED_USAGE = (
    b'data: {"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15,'
    b'"prompt_tokens_details":{"cached_tokens":4},'
    b'"completion_tokens_details":{"reasoning_tokens":2}}}\n\n'
)


class MarkerSpyRecorder:
    """UsageRecorder spy that DECLARES the full extras set incl. usage_source.

    Records every record() call's kwargs for billing-marker assertions.
    """

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
        assert self.calls, "MarkerSpyRecorder: no record() calls captured"
        return self.calls[-1]

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))


def make_use_case() -> CompletionUseCase:
    """CompletionUseCase wired with the resilient peek (mirrors streaming_resilience)."""
    return CompletionUseCase(
        FakeAuthenticator(),  # type: ignore[arg-type]
        FakeModelChecker(),  # type: ignore[arg-type]
        stream_resilience_enabled=True,
    )


def make_router(upstream: PlanStreamUpstream) -> FallbackModelRouter:
    return FallbackModelRouter(
        upstream=upstream,
        model_groups={ALIAS: [CAND_A, CAND_B]},
        stream_resilience_enabled=True,
    )


async def run_stream(upstream: PlanStreamUpstream, recorder: MarkerSpyRecorder) -> list[bytes]:
    """Drive a full stream through the use case; return the yielded chunks.

    The post-stream usage record fires (fire-and-forget) during/after drain;
    callers await settle() then read recorder.calls.
    """
    uc = make_use_case()
    gen = await uc.stream(
        raw_key="sk-test",
        body=make_payload(ALIAS),
        upstream=upstream,  # type: ignore[arg-type]
        usage_recorder=recorder,  # type: ignore[arg-type]
        model_router=make_router(upstream),
    )
    return [chunk async for chunk in gen]


@pytest.fixture
async def api_key(client: Any) -> dict[str, str]:
    """Signup → login → create key (DB-backed; distinct tenant/email). For SU6."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "StreamUsageTest",
            "email": "stream-usage-test@example.io",
            "password": "stream usage battery",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "stream-usage-test@example.io", "password": "stream usage battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "stream-usage-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
    }
