"""RED/GREEN suite for finetune-broker (/v1/fine_tuning/jobs brokered to the tenant's BYOK provider).

Contract under test (finetune-broker PLAN.md §3, DRAFT):
  POST /v1/fine_tuning/jobs   {model, training_file, ...} -> 200 FineTuningJob
       { id:"ftjob-*", object:"fine_tuning.job", model, status, training_file,
         fine_tuned_model:null, error, created_at }
       422 ERR_FINETUNE_TRAINING_FILE_INVALID | ERR_FINETUNE_MODEL_UNSUPPORTED
       402 ERR_PROVIDER_KEY_MISSING (own key only — platform fallback NEVER consulted)
  GET  /v1/fine_tuning/jobs                -> 200 {object:"list", data, has_more} tenant-scoped
  GET  /v1/fine_tuning/jobs/{id}           -> 200 | 404 ERR_FINETUNE_JOB_NOT_FOUND
  POST /v1/fine_tuning/jobs/{id}/cancel    -> 200 cancelled | 404 | 409 NOT_CANCELLABLE
  GET  /v1/fine_tuning/jobs/{id}/events    -> 200 {object:"list", data:[...event]} | 404
  POST /v1/files purpose="fine-tune"       -> 200 (additive purpose)

SECURITY INVARIANTS (HARD-STOP floor):
  T1 confused deputy — the outbound call carries ONLY the authenticated tenant's own
     credential, resolved by (authz.tenant_id, provider); never another tenant's, never
     the platform's.
  T2 training-data deputy — absent / cross-tenant / wrong-purpose training_file are ONE
     byte-identical 422 (no enumeration oracle) and produce ZERO outbound IO.
  T3 job enumeration — unknown id and cross-tenant id are byte-identical 404 on
     get/cancel/events.
  T4 credential at rest — the plaintext secret appears in NO persisted row and NO response.

RED until the finetune module + migration + files purpose seam are wired: every test targets
a route/field that does not yet exist, so the suite fails for the RIGHT reason (missing
implementation, e.g. 404/422 where the contract says 200), not a broken harness.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import text

from gateway.finetune.infrastructure.openai_client import OpenAIFinetuneClient

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers / fixtures (mirror tests/files_uploads_api/test_files_uploads_api.py)
# ---------------------------------------------------------------------------


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _signup_and_key(
    client: Any, *, tenant_name: str, email: str, password: str
) -> dict[str, str]:
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]
    token = (
        await client.post("/admin/auth/login", json={"email": email, "password": password})
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": f"ci-key-{tenant_name}"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
    }


@pytest.fixture
async def tenant_a(client: Any) -> dict[str, str]:
    return await _signup_and_key(
        client, tenant_name="FtA", email="ft-a@example.io", password="ft-a-battery-1"
    )


@pytest.fixture
async def tenant_b(client: Any) -> dict[str, str]:
    return await _signup_and_key(
        client, tenant_name="FtB", email="ft-b@example.io", password="ft-b-battery-1"
    )


_JSONL = b'{"messages":[{"role":"user","content":"hi"},{"role":"assistant","content":"yo"}]}\n'
_MODEL = "gpt-4o-mini-2024-07-18"


async def _upload_training_file(client: Any, key: str, *, purpose: str = "fine-tune") -> str:
    """Upload a JSONL training file; returns the wire file id (file-*)."""
    resp = await client.post(
        "/v1/files",
        files={"file": ("train.jsonl", _JSONL, "application/jsonl")},
        data={"purpose": purpose},
        headers=_bearer(key),
    )
    assert resp.status_code == 200, (
        f"training-file upload (purpose={purpose}) failed — M8 additive purpose missing? "
        f"{resp.status_code}: {resp.text}"
    )
    return resp.json()["id"]


async def _create_job(client: Any, key: str, *, training_file: str, model: str = _MODEL) -> Any:
    return await client.post(
        "/v1/fine_tuning/jobs",
        json={"model": model, "training_file": training_file},
        headers=_bearer(key),
    )


# ---------------------------------------------------------------------------
# Fakes — the FinetuneProviderPort seam (app.state.finetune_provider) and a
# per-tenant recording credential resolver (app.state.tenant_credential_resolver)
# ---------------------------------------------------------------------------


@dataclass
class _PortCall:
    op: str
    secret: str
    payload: dict[str, Any] = field(default_factory=dict)
    # HEAL (D1): the tenant_id the broker threaded through to this call — additive
    # field (default None), so no existing assertion on _PortCall's other fields
    # changes shape; new D1 coverage reads this to prove per-call tenant identity.
    tenant_id: Any = None


class FakeFinetuneProvider:
    """In-memory FinetuneProviderPort recording every call + the credential used.

    HEAL (D1): submit/poll/cancel now take ``tenant_id`` FIRST (matches the healed
    ``FinetuneProviderPort`` protocol) — additive to the fake's own behavior, every
    existing assertion on ``.calls[i].op/.secret/.payload`` is unchanged.
    """

    def __init__(self) -> None:
        self.calls: list[_PortCall] = []
        self.fail_submit = False
        self.fail_cancel = False
        self.poll_status: str = "running"
        self.poll_fine_tuned_model: str | None = None

    @staticmethod
    def _secret_of(credential: object) -> str:
        return str(getattr(credential, "secret", credential))

    async def submit(self, tenant_id: object, credential: object, request: dict[str, Any]) -> str:
        self.calls.append(
            _PortCall("submit", self._secret_of(credential), dict(request), tenant_id)
        )
        if self.fail_submit:
            raise ConnectionError("injected submit failure")
        return f"ftjob-provider-{uuid.uuid4().hex[:8]}"

    async def poll(
        self, tenant_id: object, credential: object, provider_job_id: str
    ) -> dict[str, Any]:
        self.calls.append(_PortCall("poll", self._secret_of(credential), tenant_id=tenant_id))
        return {
            "status": self.poll_status,
            "fine_tuned_model": self.poll_fine_tuned_model,
        }

    async def cancel(self, tenant_id: object, credential: object, provider_job_id: str) -> None:
        self.calls.append(_PortCall("cancel", self._secret_of(credential), tenant_id=tenant_id))
        if self.fail_cancel:
            raise ConnectionError("injected cancel failure")


class RecordingPerTenantResolver:
    """Structural TenantCredentialResolver double: DISTINCT secret per tenant, fail-closed.

    HEAL (D2): ``fail_corrupt_for`` additively simulates a corrupted stored
    credential (Fernet decrypt failure) by raising ``ProviderCredentialError``
    instead of ``ProviderKeyMissing`` — a DIFFERENT exception type the use-case must
    also catch (it previously did not).
    """

    def __init__(self) -> None:
        self.secrets: dict[str, str] = {}
        self.fail_for: set[str] = set()
        self.fail_corrupt_for: set[str] = set()

    def secret_for(self, tenant_id: str) -> str:
        return self.secrets.setdefault(tenant_id, f"sk-tenant-{tenant_id}-{uuid.uuid4().hex[:6]}")

    async def resolve(self, tenant_id: object, provider: str) -> object:
        from gateway.proxy.domain.provider_credentials import (
            BearerCredential,
            ProviderCredentialError,
            ProviderKeyMissing,
        )

        tid = str(tenant_id)
        if tid in self.fail_corrupt_for:
            raise ProviderCredentialError("ERR_PROVIDER_CREDENTIAL_CORRUPT")
        if tid in self.fail_for:
            raise ProviderKeyMissing(provider)
        return BearerCredential(secret=self.secret_for(tid))


class RecordingCompletionListener:
    """The finetune-model-registry extension-point double."""

    def __init__(self) -> None:
        self.succeeded: list[Any] = []

    async def on_succeeded(self, job: Any) -> None:
        self.succeeded.append(job)


@pytest.fixture
def provider_port(app: Any) -> FakeFinetuneProvider:
    port = FakeFinetuneProvider()
    app.state.finetune_provider = port
    return port


@pytest.fixture
def resolver(app: Any) -> RecordingPerTenantResolver:
    r = RecordingPerTenantResolver()
    app.state.tenant_credential_resolver = r
    return r


# ---------------------------------------------------------------------------
# M1/M2 — create + brokering
# ---------------------------------------------------------------------------


class TestCreateJob:
    async def test_create_job_returns_openai_wire(
        self,
        client: Any,
        app: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """M1+M2: create -> 200 FineTuningJob wire; exactly one provider submit."""
        file_id = await _upload_training_file(client, tenant_a["key"])
        resp = await _create_job(client, tenant_a["key"], training_file=file_id)
        assert resp.status_code == 200, f"create failed: {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["id"].startswith("ftjob-"), body
        assert body["object"] == "fine_tuning.job"
        assert body["model"] == _MODEL
        assert body["training_file"] == file_id
        assert body["status"] == "queued"
        assert body["fine_tuned_model"] is None
        assert isinstance(body["created_at"], int)
        submits = [c for c in provider_port.calls if c.op == "submit"]
        assert len(submits) == 1

    async def test_create_submit_failure_persists_failed_job(
        self,
        client: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """M1 honest degradation: submit failure -> persisted job with status 'failed'."""
        provider_port.fail_submit = True
        file_id = await _upload_training_file(client, tenant_a["key"])
        resp = await _create_job(client, tenant_a["key"], training_file=file_id)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "failed"
        assert body["error"] is not None
        assert "finetune_provider_unreachable" in str(body["error"]).lower()
        # the failed job is persisted — retrievable, never a silent half-write
        got = await client.get(
            f"/v1/fine_tuning/jobs/{body['id']}", headers=_bearer(tenant_a["key"])
        )
        assert got.status_code == 200
        assert got.json()["status"] == "failed"

    async def test_create_rejects_unsupported_model(
        self,
        client: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """R: model with no finetune-capable provider -> 422, zero outbound."""
        file_id = await _upload_training_file(client, tenant_a["key"])
        resp = await _create_job(
            client, tenant_a["key"], training_file=file_id, model="claude-sonnet-4-5"
        )
        assert resp.status_code == 422, resp.text
        assert "ERR_FINETUNE_MODEL_UNSUPPORTED" in resp.text
        assert provider_port.calls == []

    async def test_create_training_file_uniform_reject(
        self,
        client: Any,
        tenant_a: dict[str, str],
        tenant_b: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """T2: absent vs cross-tenant vs wrong-purpose file -> byte-identical 422, no outbound."""
        foreign_file = await _upload_training_file(client, tenant_b["key"])  # B's file
        wrong_purpose = await _upload_training_file(
            client, tenant_a["key"], purpose="user_data"
        )  # A's own, wrong purpose
        absent = f"file-{uuid.uuid4().hex}"

        own_valid = await _upload_training_file(client, tenant_a["key"])  # for validation_file leg

        responses = []
        for fid in (absent, foreign_file, wrong_purpose):
            r = await _create_job(client, tenant_a["key"], training_file=fid)
            responses.append(r)
        # A1 deputy bypass leg: valid own training_file + a bad reference as validation_file
        for fid in (absent, foreign_file, wrong_purpose):
            r = await client.post(
                "/v1/fine_tuning/jobs",
                json={"model": _MODEL, "training_file": own_valid, "validation_file": fid},
                headers=_bearer(tenant_a["key"]),
            )
            responses.append(r)

        first = responses[0]
        assert first.status_code == 422, first.text
        assert "ERR_FINETUNE_TRAINING_FILE_INVALID" in first.text
        for other in responses[1:]:
            assert other.status_code == first.status_code
            assert other.json() == first.json(), (
                "enumeration oracle: absent / cross-tenant / wrong-purpose must be "
                f"indistinguishable, got {first.json()} vs {other.json()}"
            )
        assert provider_port.calls == [], "a rejected create must produce ZERO outbound IO"

    async def test_create_no_credential_402_zero_outbound(
        self,
        client: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """M2 fail-closed: no own key -> 402; platform fallback NEVER consulted."""
        file_id = await _upload_training_file(client, tenant_a["key"])
        resolver.fail_for.add(tenant_a["tenant_id"])
        resp = await _create_job(client, tenant_a["key"], training_file=file_id)
        assert resp.status_code == 402, resp.text
        assert "ERR_PROVIDER_KEY_MISSING" in resp.text
        assert provider_port.calls == [], (
            "no outbound call may occur without the tenant's OWN credential "
            "(platform fallback is disabled for fine-tuning)"
        )


# ---------------------------------------------------------------------------
# ADVERSARIAL — confused deputy + enumeration + leak sweep
# ---------------------------------------------------------------------------


class TestSecurityInvariants:
    async def test_outbound_uses_own_tenant_credential_only(
        self,
        client: Any,
        tenant_a: dict[str, str],
        tenant_b: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """T1 confused deputy: A's submit signs with A's secret; B's with B's; never crossed."""
        file_a = await _upload_training_file(client, tenant_a["key"])
        file_b = await _upload_training_file(client, tenant_b["key"])

        resp_a = await _create_job(client, tenant_a["key"], training_file=file_a)
        calls_after_a = list(provider_port.calls)
        resp_b = await _create_job(client, tenant_b["key"], training_file=file_b)
        calls_b = provider_port.calls[len(calls_after_a) :]

        assert resp_a.status_code == 200, resp_a.text
        assert resp_b.status_code == 200, resp_b.text

        secret_a = resolver.secret_for(tenant_a["tenant_id"])
        secret_b = resolver.secret_for(tenant_b["tenant_id"])
        assert secret_a != secret_b

        assert calls_after_a, "expected at least one outbound call for tenant A"
        assert all(c.secret == secret_a for c in calls_after_a), (
            f"tenant A's outbound calls must carry ONLY A's credential: {calls_after_a}"
        )
        assert calls_b, "expected at least one outbound call for tenant B"
        assert all(c.secret == secret_b for c in calls_b), (
            f"tenant B's outbound calls must carry ONLY B's credential: {calls_b}"
        )
        # neither secret ever appears in a response body
        for r, s in ((resp_a, secret_a), (resp_b, secret_b), (resp_a, secret_b), (resp_b, secret_a)):
            assert s not in r.text

    async def test_get_cross_tenant_404_byte_identical(
        self,
        client: Any,
        tenant_a: dict[str, str],
        tenant_b: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """T3: B on A's job id vs an unknown id -> byte-identical 404 (get/cancel/events)."""
        file_a = await _upload_training_file(client, tenant_a["key"])
        created = await _create_job(client, tenant_a["key"], training_file=file_a)
        assert created.status_code == 200, created.text
        job_id = created.json()["id"]
        unknown = f"ftjob-{uuid.uuid4().hex}"

        for path in ("{}", "{}/events"):
            cross = await client.get(
                f"/v1/fine_tuning/jobs/{path.format(job_id)}", headers=_bearer(tenant_b["key"])
            )
            missing = await client.get(
                f"/v1/fine_tuning/jobs/{path.format(unknown)}", headers=_bearer(tenant_b["key"])
            )
            assert cross.status_code == 404, f"{path}: {cross.status_code}: {cross.text}"
            assert missing.status_code == 404
            assert cross.json() == missing.json(), (
                f"enumeration oracle on {path}: cross-tenant and unknown must be identical"
            )
            assert "ERR_FINETUNE_JOB_NOT_FOUND" in cross.text

        cross_cancel = await client.post(
            f"/v1/fine_tuning/jobs/{job_id}/cancel", headers=_bearer(tenant_b["key"])
        )
        missing_cancel = await client.post(
            f"/v1/fine_tuning/jobs/{unknown}/cancel", headers=_bearer(tenant_b["key"])
        )
        assert cross_cancel.status_code == 404
        assert cross_cancel.json() == missing_cancel.json()
        # and A's job is untouched by B's cancel attempt
        mine = await client.get(f"/v1/fine_tuning/jobs/{job_id}", headers=_bearer(tenant_a["key"]))
        assert mine.status_code == 200
        assert mine.json()["status"] != "cancelled"

    async def test_credential_never_persisted(
        self,
        client: Any,
        app: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """T4: the plaintext secret appears in ZERO finetune rows and no response body."""
        file_a = await _upload_training_file(client, tenant_a["key"])
        created = await _create_job(client, tenant_a["key"], training_file=file_a)
        assert created.status_code == 200, created.text
        secret = resolver.secret_for(tenant_a["tenant_id"])
        assert secret not in created.text

        async with app.state.sessionmaker() as session:
            for table in ("finetune_jobs", "finetune_job_events"):
                rows = (await session.execute(text(f"SELECT * FROM {table}"))).mappings().all()
                for row in rows:
                    assert secret not in repr(dict(row)), (
                        f"plaintext credential leaked into {table}: {row}"
                    )


# ---------------------------------------------------------------------------
# M3/M4/M5/M7 — list · cancel · events · completion extension point
# ---------------------------------------------------------------------------


class TestJobLifecycle:
    async def test_list_tenant_scoped(
        self,
        client: Any,
        tenant_a: dict[str, str],
        tenant_b: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """M3: each tenant lists ONLY its own jobs, list wire shape."""
        file_a = await _upload_training_file(client, tenant_a["key"])
        file_b = await _upload_training_file(client, tenant_b["key"])
        job_a = (await _create_job(client, tenant_a["key"], training_file=file_a)).json()["id"]
        job_b = (await _create_job(client, tenant_b["key"], training_file=file_b)).json()["id"]

        resp = await client.get("/v1/fine_tuning/jobs", headers=_bearer(tenant_a["key"]))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["object"] == "list"
        assert "has_more" in body
        ids = [j["id"] for j in body["data"]]
        assert job_a in ids
        assert job_b not in ids, "cross-tenant job leaked into the list"

    async def test_cancel_non_terminal_job(
        self,
        client: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """M4: cancel a queued job -> 200 cancelled; provider cancel invoked."""
        file_a = await _upload_training_file(client, tenant_a["key"])
        job_id = (await _create_job(client, tenant_a["key"], training_file=file_a)).json()["id"]
        resp = await client.post(
            f"/v1/fine_tuning/jobs/{job_id}/cancel", headers=_bearer(tenant_a["key"])
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "cancelled"
        assert any(c.op == "cancel" for c in provider_port.calls)

    async def test_cancel_terminal_conflict(
        self,
        client: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """R: cancel on a terminal (succeeded) job -> 409, status unchanged."""
        file_a = await _upload_training_file(client, tenant_a["key"])
        job_id = (await _create_job(client, tenant_a["key"], training_file=file_a)).json()["id"]
        # drive terminal via refresh-on-read
        provider_port.poll_status = "succeeded"
        provider_port.poll_fine_tuned_model = f"ft:{_MODEL}:acme::{uuid.uuid4().hex[:6]}"
        got = await client.get(f"/v1/fine_tuning/jobs/{job_id}", headers=_bearer(tenant_a["key"]))
        assert got.status_code == 200 and got.json()["status"] == "succeeded", got.text

        resp = await client.post(
            f"/v1/fine_tuning/jobs/{job_id}/cancel", headers=_bearer(tenant_a["key"])
        )
        assert resp.status_code == 409, resp.text
        assert "ERR_FINETUNE_JOB_NOT_CANCELLABLE" in resp.text
        still = await client.get(f"/v1/fine_tuning/jobs/{job_id}", headers=_bearer(tenant_a["key"]))
        assert still.json()["status"] == "succeeded"

    async def test_cancel_provider_unreachable_502_status_unchanged(
        self,
        client: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """R: provider down during cancel -> 502, job status UNCHANGED (cancel stays retryable)."""
        file_a = await _upload_training_file(client, tenant_a["key"])
        job_id = (await _create_job(client, tenant_a["key"], training_file=file_a)).json()["id"]
        provider_port.fail_cancel = True
        resp = await client.post(
            f"/v1/fine_tuning/jobs/{job_id}/cancel", headers=_bearer(tenant_a["key"])
        )
        assert resp.status_code == 502, resp.text
        assert "ERR_FINETUNE_PROVIDER_UNREACHABLE" in resp.text
        still = await client.get(f"/v1/fine_tuning/jobs/{job_id}", headers=_bearer(tenant_a["key"]))
        assert still.status_code == 200
        assert still.json()["status"] == "queued", "a failed cancel must not move the status"

    async def test_missing_or_invalid_key_401(self, client: Any) -> None:
        """R: the existing 401 auth contract holds on every new route, unchanged."""
        no_key = await client.get("/v1/fine_tuning/jobs")
        assert no_key.status_code == 401
        bad_key = await client.post(
            "/v1/fine_tuning/jobs",
            json={"model": _MODEL, "training_file": "file-deadbeef"},
            headers=_bearer("sk-invalid-nope"),
        )
        assert bad_key.status_code == 401

    async def test_events_recorded_and_scoped(
        self,
        client: Any,
        tenant_a: dict[str, str],
        tenant_b: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """M5: lifecycle events on the OpenAI event wire; cross-tenant events -> 404."""
        file_a = await _upload_training_file(client, tenant_a["key"])
        job_id = (await _create_job(client, tenant_a["key"], training_file=file_a)).json()["id"]
        resp = await client.get(
            f"/v1/fine_tuning/jobs/{job_id}/events", headers=_bearer(tenant_a["key"])
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["object"] == "list"
        assert len(body["data"]) >= 1, "expected at least a 'created' lifecycle event"
        for ev in body["data"]:
            assert ev["object"] == "fine_tuning.job.event"
            assert ev["id"].startswith("ftevent-")
            assert ev["level"] in ("info", "warn", "error")
            assert isinstance(ev["created_at"], int)

        foreign = await client.get(
            f"/v1/fine_tuning/jobs/{job_id}/events", headers=_bearer(tenant_b["key"])
        )
        assert foreign.status_code == 404

    async def test_succeeded_persists_model_and_fires_listener(
        self,
        client: Any,
        app: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """M7: terminal succeeded persists fine_tuned_model + invokes the registry listener once."""
        listener = RecordingCompletionListener()
        app.state.finetune_completion_listener = listener

        file_a = await _upload_training_file(client, tenant_a["key"])
        job_id = (await _create_job(client, tenant_a["key"], training_file=file_a)).json()["id"]

        ft_model = f"ft:{_MODEL}:acme::{uuid.uuid4().hex[:6]}"
        provider_port.poll_status = "succeeded"
        provider_port.poll_fine_tuned_model = ft_model

        got = await client.get(f"/v1/fine_tuning/jobs/{job_id}", headers=_bearer(tenant_a["key"]))
        assert got.status_code == 200, got.text
        assert got.json()["status"] == "succeeded"
        assert got.json()["fine_tuned_model"] == ft_model
        assert len(listener.succeeded) == 1, (
            "the finetune-model-registry extension point must fire exactly once"
        )

        # persisted, not just projected: a second read (provider now irrelevant) still serves it
        provider_port.poll_status = "running"
        again = await client.get(f"/v1/fine_tuning/jobs/{job_id}", headers=_bearer(tenant_a["key"]))
        assert again.json()["fine_tuned_model"] == ft_model
        assert again.json()["status"] == "succeeded"
        assert len(listener.succeeded) == 1, "terminal jobs are never re-polled or re-fired"


# ---------------------------------------------------------------------------
# M8 — /v1/files additive purpose
# ---------------------------------------------------------------------------


class TestFilesFineTunePurpose:
    async def test_files_purpose_fine_tune_accepted(
        self, client: Any, tenant_a: dict[str, str]
    ) -> None:
        """M8: purpose='fine-tune' accepted additively; unknown purposes still 422."""
        resp = await client.post(
            "/v1/files",
            files={"file": ("train.jsonl", _JSONL, "application/jsonl")},
            data={"purpose": "fine-tune"},
            headers=_bearer(tenant_a["key"]),
        )
        assert resp.status_code == 200, f"purpose fine-tune must be accepted: {resp.text}"
        assert resp.json()["purpose"] == "fine-tune"

        bogus = await client.post(
            "/v1/files",
            files={"file": ("x.txt", b"x", "text/plain")},
            data={"purpose": "bogus"},
            headers=_bearer(tenant_a["key"]),
        )
        assert bogus.status_code == 422
        assert "ERR_FILE_PURPOSE_UNSUPPORTED" in bogus.text


# ---------------------------------------------------------------------------
# HEAL (2026-07-24) — additive coverage for 4 defects a dual security refute-read
# found in build 58ca854. Existing 16 tests above are UNCHANGED; this section only
# ADDS new red->green coverage. See use_cases.py module docstring "HEAL" note.
# ---------------------------------------------------------------------------


class _FlakyOpenAIFinetuneClient(OpenAIFinetuneClient):
    """D1 test double: subclasses the REAL ``OpenAIFinetuneClient`` so it reuses the
    ACTUAL production per-tenant ``CircuitBreaker`` registry (``_breaker_for``) —
    only the HTTP transport is replaced with a controllable stub. This proves the
    PRODUCTION breaker code isolates failures per tenant; a dumb fake with no
    breaker at all (``FakeFinetuneProvider``) could never expose this defect.
    """

    def __init__(self) -> None:
        super().__init__()
        self.fail_for: set[str] = set()

    async def submit(self, tenant_id: object, credential: object, request: dict[str, Any]) -> str:
        breaker = self._breaker_for(tenant_id)  # type: ignore[arg-type]
        breaker.guard()  # raises CircuitOpenError if THIS tenant's breaker is open
        if str(tenant_id) in self.fail_for:
            breaker.on_upstream_error()
            raise ConnectionError("stub upstream failure")
        breaker.record_success()
        return f"ftjob-provider-{uuid.uuid4().hex[:8]}"

    async def cancel(self, tenant_id: object, credential: object, provider_job_id: str) -> None:
        breaker = self._breaker_for(tenant_id)  # type: ignore[arg-type]
        breaker.guard()
        if str(tenant_id) in self.fail_for:
            breaker.on_upstream_error()
            raise ConnectionError("stub upstream failure")
        breaker.record_success()


class TestHealD1PerTenantBreaker:
    async def test_tripped_breaker_does_not_502_other_tenant(
        self,
        client: Any,
        app: Any,
        tenant_a: dict[str, str],
        tenant_b: dict[str, str],
    ) -> None:
        """D1: tenant A's breaker tripping (5 consecutive submit failures — the real
        CircuitBreaker's _FAILURE_THRESHOLD) must NOT 502/degrade tenant B's
        fine-tuning — proves the per-tenant breaker registry, not a shared one."""
        flaky = _FlakyOpenAIFinetuneClient()
        app.state.finetune_provider = flaky
        flaky.fail_for.add(tenant_a["tenant_id"])

        # Trip tenant A's breaker: 5 consecutive submit failures.
        for _ in range(5):
            file_id = await _upload_training_file(client, tenant_a["key"])
            resp = await _create_job(client, tenant_a["key"], training_file=file_id)
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "failed"

        # A's breaker is now OPEN — a further call for A still honestly degrades
        # (never a 500/uncaught error), status quo for A.
        file_id = await _upload_training_file(client, tenant_a["key"])
        resp = await _create_job(client, tenant_a["key"], training_file=file_id)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "failed"

        # Tenant B — a DIFFERENT tenant, never in fail_for — must succeed normally:
        # its breaker was never touched by A's failures (this is the D1 assertion).
        file_b = await _upload_training_file(client, tenant_b["key"])
        resp_b = await _create_job(client, tenant_b["key"], training_file=file_b)
        assert resp_b.status_code == 200, resp_b.text
        assert resp_b.json()["status"] == "queued", (
            "tenant B's fine-tuning must not be affected by tenant A's tripped breaker"
        )


class TestHealD2CorruptedCredential:
    async def test_create_with_corrupted_credential_returns_402(
        self,
        client: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """D2: a corrupted stored credential (ProviderCredentialError, e.g. a Fernet
        decrypt failure) must fail-closed 402 on create — previously uncaught,
        propagating as an unhandled 500."""
        resolver.fail_corrupt_for.add(tenant_a["tenant_id"])
        file_id = await _upload_training_file(client, tenant_a["key"])
        resp = await _create_job(client, tenant_a["key"], training_file=file_id)
        assert resp.status_code == 402, resp.text
        assert provider_port.calls == [], "no outbound call on a corrupted credential"

    async def test_cancel_with_corrupted_credential_returns_402(
        self,
        client: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """D2: cancel also fail-closes 402 on a corrupted credential (status unchanged)."""
        file_id = await _upload_training_file(client, tenant_a["key"])
        job_id = (await _create_job(client, tenant_a["key"], training_file=file_id)).json()["id"]
        resolver.fail_corrupt_for.add(tenant_a["tenant_id"])
        resp = await client.post(
            f"/v1/fine_tuning/jobs/{job_id}/cancel", headers=_bearer(tenant_a["key"])
        )
        assert resp.status_code == 402, resp.text

    async def test_get_with_corrupted_credential_is_stale_ok(
        self,
        client: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """D2: GET refresh-on-read with a corrupted credential is stale-ok — never a
        5xx (or 402) on a read (M7's read-path guarantee)."""
        file_id = await _upload_training_file(client, tenant_a["key"])
        job_id = (await _create_job(client, tenant_a["key"], training_file=file_id)).json()["id"]
        resolver.fail_corrupt_for.add(tenant_a["tenant_id"])
        resp = await client.get(
            f"/v1/fine_tuning/jobs/{job_id}", headers=_bearer(tenant_a["key"])
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "queued"


class TestHealD3SubmitFailureLeakSweep:
    async def test_submit_failure_leaves_no_secret_in_error_or_events(
        self,
        client: Any,
        app: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """D3: sweep finetune_jobs.error + finetune_job_events.data after a
        submit-time provider failure — the credential must appear NOWHERE. Locks
        the invariant against a future adapter change that might echo raw
        exception/response text containing the Authorization header."""
        provider_port.fail_submit = True
        file_id = await _upload_training_file(client, tenant_a["key"])
        resp = await _create_job(client, tenant_a["key"], training_file=file_id)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "failed"
        secret = resolver.secret_for(tenant_a["tenant_id"])
        assert secret not in resp.text

        async with app.state.sessionmaker() as session:
            error_rows = (
                await session.execute(text("SELECT error FROM finetune_jobs"))
            ).scalars().all()
            for err in error_rows:
                assert err is None or secret not in err, (
                    f"credential leaked into finetune_jobs.error: {err}"
                )
            event_rows = (
                await session.execute(text("SELECT data FROM finetune_job_events"))
            ).scalars().all()
            for data in event_rows:
                assert secret not in str(data), (
                    f"credential leaked into finetune_job_events.data: {data}"
                )


class ThrowingCompletionListener:
    """D4 test double: the finetune-model-registry extension point, but it always
    raises — proves a broken registry write can never roll back the broker's own
    already-decided terminal state."""

    async def on_succeeded(self, job: Any) -> None:
        raise RuntimeError("simulated finetune-model-registry write failure")


class TestHealD4ListenerRollback:
    async def test_throwing_listener_does_not_roll_back_terminal_cas(
        self,
        client: Any,
        app: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """D4: a throwing completion listener must NOT roll back the terminal CAS —
        the job reaches "succeeded" on the FIRST read (no 500) and STAYS there (a
        second read must not re-poll the provider or lose fine_tuned_model)."""
        app.state.finetune_completion_listener = ThrowingCompletionListener()

        file_id = await _upload_training_file(client, tenant_a["key"])
        job_id = (await _create_job(client, tenant_a["key"], training_file=file_id)).json()["id"]

        ft_model = f"ft:{_MODEL}:acme::{uuid.uuid4().hex[:6]}"
        provider_port.poll_status = "succeeded"
        provider_port.poll_fine_tuned_model = ft_model

        got = await client.get(
            f"/v1/fine_tuning/jobs/{job_id}", headers=_bearer(tenant_a["key"])
        )
        assert got.status_code == 200, got.text
        assert got.json()["status"] == "succeeded"
        assert got.json()["fine_tuned_model"] == ft_model

        # A SECOND read: flip poll_status back to "running" — if the CAS had been
        # rolled back by the throwing listener, this read would re-poll the
        # provider and observe "running" again (or worse, crash). It must not.
        provider_port.poll_status = "running"
        again = await client.get(
            f"/v1/fine_tuning/jobs/{job_id}", headers=_bearer(tenant_a["key"])
        )
        assert again.status_code == 200
        assert again.json()["status"] == "succeeded"
        assert again.json()["fine_tuned_model"] == ft_model
