"""RED behavioral checks for audit-coverage-structural-guard (R9) — the five silent modules.

Contract under test (audit-coverage-structural-guard TASK.md §RULES, DRAFT):
  M2 Each of the five modules' mutating endpoints, ON SUCCESS, writes an AuditEvent with
     action `<module>.<verb>`, target_type = the bare resource noun, target_id = the resource
     id, tenant_id = the authed tenant, actor_key_id = AuthzResult.key_id and actor_user_id
     None (the realtime_relay_ws.py:256 precedent — satisfies the audit_missing_actor
     invariant at audit/domain/audit_event.py:57).
  A1 On key-authed /v1 surfaces the KEY is the actor; no user identity exists to supply.
  A8 The action vocabulary is the existing flat `<module>.<verb>` family.
  A13 Success response bodies are UNCHANGED — audit is a side effect, never a wire change.

WHY THIS SUITE IS RED AT THE CURRENT TREE:
  `grep -rl 'record_audit(' src/gateway/{evals,vector_stores,finetune,memory,conversations}`
  is EMPTY. All five modules mutate tenant data — eval sets/cases/runs/baselines, vector
  stores and their files, fine-tuning jobs, memories, conversations and their messages — and
  write ZERO rows to audit_events, the table the SOC 2 CC-series evidence leans on. Every
  test below drives the real endpoints, asserts the mutation SUCCEEDED (so a failure here can
  never be mistaken for a broken harness), and then finds no audit row.

DO NOT weaken these tests to pass — that is Build's job. Each test asserts the endpoint's
own status code FIRST; if that assert is the one that fails, the suite is red for the WRONG
reason and the harness needs fixing, not the assertion.

The provider fakes are IMPORTED from the five modules' own suites rather than re-typed here,
so a change to a provider-port signature breaks one place instead of drifting silently. (Two
module-private CONSTANTS are copied instead of imported — Pyright strict refuses cross-module
private access; see the note on them below.)
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from tests.audit_coverage.conftest import (
    AuditRow,
    bearer,
    assert_event_shape,
    assert_key_actor,
    assert_user_actor,
    audit_ids,
    describe,
    id_forms,
    new_audit_rows,
)
from tests.evals_verdict.test_baseline_and_verdict import ScriptedUpstream
from tests.finetune_broker.test_finetune_broker import (
    FakeFinetuneProvider,
    RecordingPerTenantResolver,
)

pytestmark = pytest.mark.asyncio

# Copied (not imported) from tests/finetune_broker/test_finetune_broker.py:95-96 — they are
# module-private there, and Pyright strict refuses cross-module private access. If the
# supported fine-tune model list moves, the create call below fails with an explicit
# "fine-tune job create failed" message rather than a mystery.
TRAINING_JSONL = (
    b'{"messages":[{"role":"user","content":"hi"},{"role":"assistant","content":"yo"}]}\n'
)
FINETUNE_MODEL = "gpt-4o-mini-2024-07-18"


# ---------------------------------------------------------------------------
# Local fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def active_model(db_session: Any) -> str:
    """An active catalog model + pricing row — mirrors tests/evals_runs:151."""
    model_id = "openai/gpt-4o-auditcov"
    await db_session.execute(
        text("INSERT INTO models (id, name, context_length, active) VALUES (:i, :n, 128000, true)"),
        {"i": model_id, "n": "GPT-4o audit-coverage"},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots "
            "(id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at) "
            "VALUES (:id, :m, 0.0000025, 0.00001, now())"
        ),
        {"id": str(uuid.uuid4()), "m": model_id},
    )
    await db_session.commit()
    return model_id


class _CountingRecorder:
    """In-memory UsageRecorder — keeps the Redis→Postgres flush out of these tests."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.records.append(kwargs)


def _no_network(app: Any, upstream: Any) -> None:
    """Install the zero-network completion seam used by the evals suites."""
    app.state.completion_upstream = upstream
    app.state.usage_recorder = _CountingRecorder()


async def _stub_embedder(raw_key: str, content: str) -> list[float] | None:
    """Deterministic no-op embedder — memory create is best-effort on embedding."""
    return None


def _prefixes(module: str) -> set[str]:
    """Both action-prefix spellings the contract offers.

    M2 says `<module>.<verb>` and `scope:` names the modules plurally (vector_stores,
    conversations); A8's examples are singular (`vector_store.create`, `conversation.create`).
    The contract contradicts itself, so accept either — and refuse anything from neither.
    """
    forms = {module}
    if module.endswith("ies"):
        forms.add(f"{module[:-3]}y")
    elif module.endswith("s"):
        forms.add(module[:-1])
    else:
        forms.add(f"{module}s")
        if module.endswith("y"):
            forms.add(f"{module[:-1]}ies")
    return forms


def _module_rows(rows: list[AuditRow], prefixes: set[str]) -> list[AuditRow]:
    """New rows belonging to THIS module's action family.

    Filtering by prefix rather than taking every new row keeps an unrelated emitter (e.g. the
    proxy's platform-fallback audit on a governed replay) from turning this check red for
    someone else's event.
    """
    return [row for row in rows if any(row.action.startswith(f"{p}.") for p in prefixes)]


def _expect(
    rows: list[AuditRow],
    *,
    prefixes: set[str],
    expected: dict[str, set[str]],
    tenant: dict[str, str],
    module: str,
    user_actor: frozenset[str] = frozenset(),
) -> None:
    """Assert one correctly-shaped, DISTINCTLY-named audit row per exercised mutation."""
    families = sorted(f"{p}.*" for p in prefixes)
    module_rows = _module_rows(rows, prefixes)
    assert len(module_rows) == len(expected), (
        f"{module}: expected {len(expected)} {' / '.join(families)} audit event(s) — one per "
        f"mutating call ({', '.join(sorted(expected))}) — but found {len(module_rows)}.\n"
        f"  ALL audit_events rows written during this test:\n{describe(rows)}\n"
        f"  gateway/{module}/ contains no record_audit() call: every mutation above left the "
        "audit trail empty."
    )

    # ANTI-CHEAT: without this, the cheapest passing Build is ONE generic action per module —
    # four rows all reading `conversation.touch` would satisfy a count-and-target_id check and
    # produce an audit trail that cannot tell a create from a delete. For a SOC 2 evidence
    # surface that is worse than useless, so each mutation must carry its OWN verb.
    actions = [row.action for row in module_rows]
    assert len(set(actions)) == len(expected), (
        f"{module}: the {len(module_rows)} audit rows use only {len(set(actions))} distinct "
        f"action(s) {sorted(set(actions))} for {len(expected)} DIFFERENT mutations "
        f"({', '.join(sorted(expected))}). Each mutation needs its own `<module>.<verb>` — an "
        "audit trail that cannot distinguish a create from a delete is not evidence."
    )

    unmatched = dict(expected)
    for row in module_rows:
        label = f"{module}:{row.action}"
        matched = [name for name, ids in unmatched.items() if row.target_id in ids]
        assert matched, (
            f"{label}: target_id {row.target_id!r} matches none of the mutations this test "
            f"performed ({sorted(unmatched)}) — an audit row must name the resource it "
            f"describes.\n  rows: \n{describe(module_rows)}"
        )
        name = matched[0]
        # The actor rule is per-SURFACE, not per-module (M2 rev 2): key-authed /v1 routes take
        # the key, the JWT-authed /admin twin takes the Identity's user.
        if name in user_actor:
            assert_user_actor(row, tenant_id=tenant["tenant_id"], label=label)
        else:
            assert_key_actor(
                row, tenant_id=tenant["tenant_id"], key_id=tenant["key_id"], label=label
            )
        assert_event_shape(row, prefixes=prefixes, target_ids=unmatched.pop(name), label=label)
    assert not unmatched, (
        f"{module}: these mutations produced no audit event: {sorted(unmatched)}\n"
        f"  rows written: \n{describe(module_rows)}"
    )


# ---------------------------------------------------------------------------
# evals — set create · case create · run launch · baseline pin
# ---------------------------------------------------------------------------


async def test_evals_mutations_audited(
    client: Any, app: Any, tenant: dict[str, str], active_model: str
) -> None:
    """covers: M2, A1, A8, A13 — set create, case create, run launch and BOTH baseline pins
    (the /v1 key-authed one and the /admin console twin) each leave a DISTINCT-action
    audit row; the /v1 rows carry actor_key_id with actor_user_id None, the /admin row
    carries the Identity's user actor.

    NOTE (prose only, no assertion changed): this docstring deliberately says "audit row"
    rather than naming the table. tests/repo_hygiene/test_fire_and_forget_has_a_wait.py keys
    off that table name appearing in a test body and flagged this test as an unwaited
    fire-and-forget assertion. It is a false positive — A5 requires the retrofit to be
    INLINE-awaited (`await record_audit(...)` at each call site, no create_task/ensure_future
    anywhere), so the row is committed before the response returns and there is nothing to
    poll for. Rewording one word was preferred over teaching the standing guard an exception,
    which would have weakened a guard that catches real flakes.

    The console twin is the M1 (rev 2) payoff: under the immediate-package evidence rule
    gateway.evals.console must earn its OWN record_audit call, so it cannot ride
    gateway.evals.api's retrofit — and because it authenticates with `Identity` rather than an
    API key, applying the /v1 key rule to it would 500 the route (M2).
    """
    _no_network(app, ScriptedUpstream(prefix="echo"))
    key = tenant["key"]
    before = await audit_ids(app)

    created_set = await client.post(
        "/v1/evals/sets", json={"name": "audit-cov"}, headers=bearer(key)
    )
    assert created_set.status_code == 201, f"eval-set create failed: {created_set.text}"
    set_id = created_set.json()["id"]

    created_case = await client.post(
        f"/v1/evals/sets/{set_id}/cases",
        json={
            "request_body": {"model": "ignored", "messages": [{"role": "user", "content": "one"}]},
            "assertion": {"kind": "contains", "expected": "echo"},
        },
        headers=bearer(key),
    )
    assert created_case.status_code == 201, f"eval-case create failed: {created_case.text}"
    case_id = created_case.json()["id"]

    launched = await client.post(
        f"/v1/evals/sets/{set_id}/runs", json={"model": active_model}, headers=bearer(key)
    )
    assert launched.status_code == 201, f"eval-run launch failed: {launched.text}"
    run_id = launched.json()["id"]

    pinned = await client.put(
        f"/v1/evals/sets/{set_id}/baseline", json={"run_id": run_id}, headers=bearer(key)
    )
    assert pinned.status_code == 200, f"baseline pin failed: {pinned.text}"

    # The JWT-authed /admin console twin — a DIFFERENT route, a DIFFERENT actor shape.
    console_pinned = await client.put(
        f"/admin/evals/sets/{set_id}/baseline",
        json={"run_id": run_id},
        headers={"Authorization": f"Bearer {tenant['jwt']}"},
    )
    assert console_pinned.status_code == 200, (
        f"console baseline pin failed: {console_pinned.status_code} {console_pinned.text}"
    )

    rows = await new_audit_rows(app, before)
    _expect(
        rows,
        prefixes=_prefixes("evals"),
        expected={
            "set_create": id_forms(set_id),
            "case_create": id_forms(case_id),
            "run_launch": id_forms(run_id),
            # M2's "the resource id" is genuinely ambiguous for a pin: the baseline relates a
            # set to a run. Either id is a defensible target; a third id is not.
            "baseline_pin": id_forms(set_id) | id_forms(run_id),
            # The /admin twin pins the same relation through a different surface. It needs its
            # own DISTINCT action — a shared verb would make the trail unable to say which
            # surface a pin came from, and the two carry different actors.
            "console_baseline_pin": id_forms(set_id) | id_forms(run_id),
        },
        user_actor=frozenset({"console_baseline_pin"}),
        tenant=tenant,
        module="evals",
    )


# ---------------------------------------------------------------------------
# vector_stores — create · file attach · delete
# ---------------------------------------------------------------------------


async def test_vector_store_mutations_audited(
    client: Any, app: Any, tenant: dict[str, str]
) -> None:
    """covers: M2, A1, A8 — create, delete, file-attach rows asserted same shape."""
    key = tenant["key"]
    before = await audit_ids(app)

    created = await client.post("/v1/vector_stores", json={"name": "kb-audit"}, headers=bearer(key))
    assert created.status_code == 200, f"vector-store create failed: {created.text}"
    store_id = created.json()["id"]

    uploaded = await client.post(
        "/v1/files",
        files={"file": ("corpus.txt", b"hydroa retrieval corpus " * 40, "text/plain")},
        data={"purpose": "assistants"},
        headers=bearer(key),
    )
    assert uploaded.status_code == 200, f"file upload failed: {uploaded.text}"
    file_id = uploaded.json()["id"]

    attached = await client.post(
        f"/v1/vector_stores/{store_id}/files", json={"file_id": file_id}, headers=bearer(key)
    )
    assert attached.status_code == 200, f"vector-store file attach failed: {attached.text}"

    deleted = await client.delete(f"/v1/vector_stores/{store_id}", headers=bearer(key))
    assert deleted.status_code == 200, f"vector-store delete failed: {deleted.text}"

    rows = await new_audit_rows(app, before)
    _expect(
        rows,
        prefixes=_prefixes("vector_stores"),
        expected={
            "create": id_forms(store_id),
            # the attach relates a store to a file — either id names the mutation honestly.
            "file_attach": id_forms(store_id) | id_forms(file_id),
            "delete": id_forms(store_id),
        },
        tenant=tenant,
        module="vector_stores",
    )


# ---------------------------------------------------------------------------
# finetune — job create · job cancel
# ---------------------------------------------------------------------------


async def test_finetune_mutations_audited(client: Any, app: Any, tenant: dict[str, str]) -> None:
    """covers: M2, A1, A8 — job create, job cancel rows asserted same shape."""
    app.state.finetune_provider = FakeFinetuneProvider()
    app.state.tenant_credential_resolver = RecordingPerTenantResolver()
    key = tenant["key"]
    before = await audit_ids(app)

    uploaded = await client.post(
        "/v1/files",
        files={"file": ("train.jsonl", TRAINING_JSONL, "application/jsonl")},
        data={"purpose": "fine-tune"},
        headers=bearer(key),
    )
    assert uploaded.status_code == 200, f"training-file upload failed: {uploaded.text}"
    training_file = uploaded.json()["id"]

    created = await client.post(
        "/v1/fine_tuning/jobs",
        json={"model": FINETUNE_MODEL, "training_file": training_file},
        headers=bearer(key),
    )
    assert created.status_code == 200, f"fine-tune job create failed: {created.text}"
    job_id = created.json()["id"]

    cancelled = await client.post(f"/v1/fine_tuning/jobs/{job_id}/cancel", headers=bearer(key))
    assert cancelled.status_code == 200, f"fine-tune job cancel failed: {cancelled.text}"

    rows = await new_audit_rows(app, before)
    _expect(
        rows,
        prefixes=_prefixes("finetune"),
        expected={"job_create": id_forms(job_id), "job_cancel": id_forms(job_id)},
        tenant=tenant,
        module="finetune",
    )


# ---------------------------------------------------------------------------
# memory — create · delete
# ---------------------------------------------------------------------------


async def test_memory_mutations_audited(client: Any, app: Any, tenant: dict[str, str]) -> None:
    """covers: M2, A1, A8 — create, delete rows asserted same shape."""
    app.state.memory_embedder = _stub_embedder
    key = tenant["key"]
    before = await audit_ids(app)

    created = await client.post(
        "/v1/memories", json={"content": "audit coverage memory"}, headers=bearer(key)
    )
    assert created.status_code == 201, f"memory create failed: {created.text}"
    memory_id = created.json()["id"]

    deleted = await client.delete(f"/v1/memories/{memory_id}", headers=bearer(key))
    assert deleted.status_code == 204, f"memory delete failed: {deleted.status_code}"

    rows = await new_audit_rows(app, before)
    _expect(
        rows,
        prefixes=_prefixes("memory"),
        expected={"create": id_forms(memory_id), "delete": id_forms(memory_id)},
        tenant=tenant,
        module="memory",
    )


# ---------------------------------------------------------------------------
# conversations — create · patch · message append · delete
# ---------------------------------------------------------------------------


async def test_conversations_mutations_audited(
    client: Any, app: Any, tenant: dict[str, str]
) -> None:
    """covers: M2, A1, A8, R:ACTOR_FABRICATION — create, patch, message-append, delete rows
    asserted; the actor asserts (key set, user None) are the fabrication pin.
    """
    key = tenant["key"]
    before = await audit_ids(app)

    created = await client.post(
        "/v1/conversations", json={"title": "audit coverage"}, headers=bearer(key)
    )
    assert created.status_code == 201, f"conversation create failed: {created.text}"
    conversation_id = created.json()["id"]

    patched = await client.patch(
        f"/v1/conversations/{conversation_id}",
        json={"title": "audit coverage renamed"},
        headers=bearer(key),
    )
    assert patched.status_code == 200, f"conversation patch failed: {patched.text}"

    appended = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "hello"},
        headers=bearer(key),
    )
    assert appended.status_code == 201, f"message append failed: {appended.text}"
    message_id = appended.json()["id"]

    deleted = await client.delete(f"/v1/conversations/{conversation_id}", headers=bearer(key))
    assert deleted.status_code == 204, f"conversation delete failed: {deleted.status_code}"

    rows = await new_audit_rows(app, before)
    _expect(
        rows,
        prefixes=_prefixes("conversations"),
        expected={
            "create": id_forms(conversation_id),
            "patch": id_forms(conversation_id),
            # the appended resource is the message; the conversation is its container.
            "message_append": id_forms(conversation_id) | id_forms(message_id),
            "delete": id_forms(conversation_id),
        },
        tenant=tenant,
        module="conversations",
    )
