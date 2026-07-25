"""RED/GREEN suite for files-uploads-api (OpenAI-wire /v1/files on the ObjectStore port).

Contract under test (files-uploads-api PLAN.md §3, DRAFT):
  POST   /v1/files              multipart {file, purpose} -> 200 File object
                                 { id:"file-*", object:"file", bytes, created_at,
                                   filename, purpose, status }
  GET    /v1/files              -> 200 { object:"list", data:[File objects] }  (NO bytes payload)
  GET    /v1/files/{id}         -> 200 File object; 404 unknown/cross-tenant
  GET    /v1/files/{id}/content -> 200 raw bytes; Content-Disposition: attachment; 404/503
  DELETE /v1/files/{id}         -> 200 { id, object:"file", deleted:true }; 404 otherwise
  POST   /v1/batches            additive: accepts input_file_id (purpose="batch") as an
                                 alternative to line_items; job created referencing the file.

TENANT-ISOLATION INVARIANT: cross-tenant access is 404 with zero data leak (never 403/200).
ANTI-ENUMERATION: a bad input_file_id on /v1/batches is ONE uniform code — absent, cross-tenant,
and wrong-purpose are indistinguishable (no oracle).

RED until the files module + batches input_file_id seam + migration are wired. Every test below
targets a route/field that does not yet exist, so the suite fails for the RIGHT reason
(missing implementation), not a broken harness.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers / fixtures (mirror apps/gateway/tests/artifacts/test_artifacts.py)
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
        client, tenant_name="FilesA", email="files-a@example.io", password="files-a-battery"
    )


@pytest.fixture
async def tenant_b(client: Any) -> dict[str, str]:
    return await _signup_and_key(
        client, tenant_name="FilesB", email="files-b@example.io", password="files-b-battery"
    )


async def _upload(
    client: Any, key: str, *, filename: str, data: bytes, purpose: str, content_type: str
) -> Any:
    return await client.post(
        "/v1/files",
        files={"file": (filename, data, content_type)},
        data={"purpose": purpose},
        headers=_bearer(key),
    )


async def _set_tenant_zdr(app: Any, tenant_id: str) -> None:
    """Flip tenants.zdr_enabled=true (mirrors retention_zdr suite's _set_tenant_zdr)."""
    async with app.state.sessionmaker() as session:
        await session.execute(
            text("UPDATE tenants SET zdr_enabled = true WHERE id = :tid"),
            {"tid": tenant_id},
        )
        await session.commit()


_JSONL = b'{"custom_id":"r1","method":"POST","url":"/v1/chat/completions","body":{}}\n'


class FakeObjectStore:
    """In-memory ObjectStore with failure injection (mirrors tests/artifacts/test_artifacts_s3.py)."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.fail_get = False

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    async def get(self, key: str) -> bytes:
        from gateway.objectstore.errors import ObjectStoreUnavailableError

        if self.fail_get:
            raise ObjectStoreUnavailableError("injected")
        return self.objects[key][0]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def health(self) -> bool:
        return not self.fail_get


# ---------------------------------------------------------------------------
# M1..M6 — /v1/files CRUD + content + purposes
# ---------------------------------------------------------------------------


class TestFilesCrud:
    async def test_upload_returns_openai_file_object(
        self, client: Any, tenant_a: dict[str, str]
    ) -> None:
        """M1: POST /v1/files multipart -> 200 File object with the OpenAI wire shape."""
        resp = await _upload(
            client,
            tenant_a["key"],
            filename="batch.jsonl",
            data=_JSONL,
            purpose="batch",
            content_type="application/jsonl",
        )
        assert resp.status_code == 200, f"upload failed: {resp.text}"
        body = resp.json()
        assert body["id"].startswith("file-"), body
        assert body["object"] == "file"
        assert body["bytes"] == len(_JSONL)
        assert body["filename"] == "batch.jsonl"
        assert body["purpose"] == "batch"
        assert isinstance(body["created_at"], int)
        assert body["status"] == "processed"

    async def test_list_files_metadata_only(
        self, client: Any, tenant_a: dict[str, str]
    ) -> None:
        """M2: GET /v1/files -> {object:'list', data:[...]}; the payload bytes are never listed."""
        up = await _upload(
            client, tenant_a["key"], filename="a.txt", data=b"hello",
            purpose="user_data", content_type="text/plain",
        )
        file_id = up.json()["id"]

        resp = await client.get("/v1/files", headers=_bearer(tenant_a["key"]))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["object"] == "list"
        ids = [f["id"] for f in body["data"]]
        assert file_id in ids
        # metadata only — raw content is never surfaced in the list envelope
        for f in body["data"]:
            assert "content" not in f
            assert "content_base64" not in f

    async def test_retrieve_file_object(self, client: Any, tenant_a: dict[str, str]) -> None:
        """M3: GET /v1/files/{id} -> the File object."""
        up = await _upload(
            client, tenant_a["key"], filename="v.png", data=b"\x89PNG\r\n",
            purpose="vision", content_type="image/png",
        )
        file_id = up.json()["id"]
        resp = await client.get(f"/v1/files/{file_id}", headers=_bearer(tenant_a["key"]))
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == file_id
        assert resp.json()["purpose"] == "vision"

    async def test_download_content_roundtrip(
        self, client: Any, tenant_a: dict[str, str]
    ) -> None:
        """M4: GET /v1/files/{id}/content -> exact bytes, attachment disposition (XSS guard)."""
        raw = b"line-one\nline-two\n"
        up = await _upload(
            client, tenant_a["key"], filename="d.txt", data=raw,
            purpose="user_data", content_type="text/plain",
        )
        file_id = up.json()["id"]
        resp = await client.get(
            f"/v1/files/{file_id}/content", headers=_bearer(tenant_a["key"])
        )
        assert resp.status_code == 200, resp.text
        assert resp.content == raw
        assert resp.headers["content-disposition"].startswith("attachment")

    async def test_delete_file(self, client: Any, tenant_a: dict[str, str]) -> None:
        """M5: DELETE /v1/files/{id} -> {deleted:true}; the file then reads 404."""
        up = await _upload(
            client, tenant_a["key"], filename="gone.txt", data=b"bye",
            purpose="user_data", content_type="text/plain",
        )
        file_id = up.json()["id"]
        deleted = await client.delete(f"/v1/files/{file_id}", headers=_bearer(tenant_a["key"]))
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["deleted"] is True
        assert deleted.json()["id"] == file_id
        # gone for real
        again = await client.get(f"/v1/files/{file_id}", headers=_bearer(tenant_a["key"]))
        assert again.status_code == 404

    async def test_purposes_vision_and_user_data_accepted(
        self, client: Any, tenant_a: dict[str, str]
    ) -> None:
        """M6: the vision and user_data purposes are accepted alongside batch."""
        for purpose in ("vision", "user_data"):
            resp = await _upload(
                client, tenant_a["key"], filename=f"{purpose}.bin", data=b"x",
                purpose=purpose, content_type="application/octet-stream",
            )
            assert resp.status_code == 200, f"{purpose}: {resp.text}"
            assert resp.json()["purpose"] == purpose


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------


class TestFilesRejections:
    async def test_reject_unsupported_purpose(
        self, client: Any, tenant_a: dict[str, str]
    ) -> None:
        """R:purpose_unsupported — a purpose outside the vocabulary is rejected.
        NOTE (R5 reconciliation, 2026-07-24): the supported purpose set grew twice as
        R5 tasks froze — finetune-broker §3 M8 added "fine-tune", vector-store-files §3 M9
        added "assistants" (both Tin-approved). To stop this test churning each time the
        set expands, it now asserts against a value that is NOT a real OpenAI purpose and
        never will be — so it durably guards the rejection path regardless of which real
        purposes we support."""
        resp = await _upload(
            client, tenant_a["key"], filename="ft.jsonl", data=_JSONL,
            purpose="not_a_real_purpose", content_type="application/jsonl",
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "ERR_FILE_PURPOSE_UNSUPPORTED"

    async def test_reject_empty_file(self, client: Any, tenant_a: dict[str, str]) -> None:
        """R:file_empty — a zero-byte upload is rejected, no row created."""
        resp = await _upload(
            client, tenant_a["key"], filename="empty.txt", data=b"",
            purpose="user_data", content_type="text/plain",
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "ERR_FILE_EMPTY"

    async def test_reject_oversize(self, client: Any, app: Any, tenant_a: dict[str, str]) -> None:
        """R:file_too_large — over Settings.files_max_bytes -> 413, checked BEFORE any store write."""
        app.state.settings.files_max_bytes = 8  # tiny cap for this test
        resp = await _upload(
            client, tenant_a["key"], filename="big.bin", data=b"0123456789",
            purpose="user_data", content_type="application/octet-stream",
        )
        assert resp.status_code == 413, resp.text
        assert resp.json()["code"] == "ERR_FILE_TOO_LARGE"

    async def test_zdr_tenant_upload_blocked(
        self, client: Any, app: Any, tenant_a: dict[str, str]
    ) -> None:
        """R:zdr — a Zero-Data-Retention tenant's upload is fail-closed 403; no bytes persisted."""
        await _set_tenant_zdr(app, tenant_a["tenant_id"])
        resp = await _upload(
            client, tenant_a["key"], filename="z.txt", data=b"secret",
            purpose="user_data", content_type="text/plain",
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["code"] == "ERR_ZDR_PAYLOAD_BLOCKED"

    async def test_unauthenticated_401(self, client: Any) -> None:
        """R:auth — no Bearer key -> 401, uniform posture."""
        resp = await client.post(
            "/v1/files",
            files={"file": ("x.txt", b"x", "text/plain")},
            data={"purpose": "batch"},
        )
        assert resp.status_code == 401, resp.text

    async def test_content_object_store_unavailable_is_503(
        self, client: Any, app: Any, tenant_a: dict[str, str]
    ) -> None:
        """R:object_store_unavailable — an s3-backed file whose store is unreachable on read
        returns an HONEST 503, NEVER a 404 lie or an empty 200 (Ground: honest degradation)."""
        store = FakeObjectStore()
        app.state.object_store = store  # force the s3 byte path on upload
        up = await _upload(
            client, tenant_a["key"], filename="s3.bin", data=b"payload-bytes",
            purpose="user_data", content_type="application/octet-stream",
        )
        assert up.status_code == 200, up.text
        file_id = up.json()["id"]
        store.fail_get = True  # store goes unreachable
        resp = await client.get(
            f"/v1/files/{file_id}/content", headers=_bearer(tenant_a["key"])
        )
        assert resp.status_code == 503, resp.text
        assert resp.json()["code"] == "ERR_OBJECT_STORE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Tenant isolation — cross-tenant is always 404 (never a leak)
# ---------------------------------------------------------------------------


class TestFilesTenantIsolation:
    async def test_cross_tenant_retrieve_is_404(
        self, client: Any, tenant_a: dict[str, str], tenant_b: dict[str, str]
    ) -> None:
        up = await _upload(
            client, tenant_a["key"], filename="a.txt", data=b"a-only",
            purpose="user_data", content_type="text/plain",
        )
        file_id = up.json()["id"]
        resp = await client.get(f"/v1/files/{file_id}", headers=_bearer(tenant_b["key"]))
        assert resp.status_code == 404, resp.text

    async def test_cross_tenant_content_is_404(
        self, client: Any, tenant_a: dict[str, str], tenant_b: dict[str, str]
    ) -> None:
        up = await _upload(
            client, tenant_a["key"], filename="a.txt", data=b"a-only",
            purpose="user_data", content_type="text/plain",
        )
        file_id = up.json()["id"]
        resp = await client.get(
            f"/v1/files/{file_id}/content", headers=_bearer(tenant_b["key"])
        )
        assert resp.status_code == 404, resp.text

    async def test_cross_tenant_delete_is_404(
        self, client: Any, tenant_a: dict[str, str], tenant_b: dict[str, str]
    ) -> None:
        up = await _upload(
            client, tenant_a["key"], filename="a.txt", data=b"a-only",
            purpose="user_data", content_type="text/plain",
        )
        file_id = up.json()["id"]
        resp = await client.delete(f"/v1/files/{file_id}", headers=_bearer(tenant_b["key"]))
        assert resp.status_code == 404, resp.text
        # and tenant A can still read it (delete never happened)
        still = await client.get(f"/v1/files/{file_id}", headers=_bearer(tenant_a["key"]))
        assert still.status_code == 200, still.text


# ---------------------------------------------------------------------------
# M7 — /v1/batches consumes input_file_id (the shared seam; JSONL EXPANSION is v58)
# ---------------------------------------------------------------------------


class TestBatchesInputFileId:
    async def test_batches_accepts_input_file_id(
        self, client: Any, tenant_a: dict[str, str]
    ) -> None:
        """M7: a purpose='batch' file drives a /v1/batches job via input_file_id.

        This task contracts the REFERENCE + validation + job creation; the JSONL ->
        line-item EXPANSION is v58's scope. The job is created referencing the file.
        """
        up = await _upload(
            client, tenant_a["key"], filename="in.jsonl", data=_JSONL,
            purpose="batch", content_type="application/jsonl",
        )
        file_id = up.json()["id"]

        resp = await client.post(
            "/v1/batches",
            json={
                "input_file_id": file_id,
                "endpoint": "/v1/chat/completions",
                "completion_window": "24h",
            },
            headers=_bearer(tenant_a["key"]),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "id" in body
        assert body["input_file_id"] == file_id  # additive field echoing the reference

        # the created job is pollable, tenant-scoped
        got = await client.get(f"/v1/batches/{body['id']}", headers=_bearer(tenant_a["key"]))
        assert got.status_code == 200, got.text

    async def test_batches_input_file_wrong_purpose_rejected(
        self, client: Any, tenant_a: dict[str, str]
    ) -> None:
        """R:batch_input_invalid — a non-'batch' file as input_file_id is rejected uniformly."""
        up = await _upload(
            client, tenant_a["key"], filename="v.png", data=b"\x89PNG",
            purpose="vision", content_type="image/png",
        )
        resp = await client.post(
            "/v1/batches",
            json={"input_file_id": up.json()["id"], "endpoint": "/v1/chat/completions",
                  "completion_window": "24h"},
            headers=_bearer(tenant_a["key"]),
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "ERR_BATCH_INPUT_FILE_INVALID"

    async def test_batches_input_file_cross_tenant_rejected_uniformly(
        self, client: Any, tenant_a: dict[str, str], tenant_b: dict[str, str]
    ) -> None:
        """R:batch_input_invalid + isolation — tenant B referencing tenant A's file gets the
        SAME code as an absent file (no oracle distinguishing exists-elsewhere from absent)."""
        up = await _upload(
            client, tenant_a["key"], filename="in.jsonl", data=_JSONL,
            purpose="batch", content_type="application/jsonl",
        )
        a_file_id = up.json()["id"]

        cross = await client.post(
            "/v1/batches",
            json={"input_file_id": a_file_id, "endpoint": "/v1/chat/completions",
                  "completion_window": "24h"},
            headers=_bearer(tenant_b["key"]),
        )
        absent = await client.post(
            "/v1/batches",
            json={"input_file_id": "file-deadbeefdeadbeefdeadbeefdeadbeef",
                  "endpoint": "/v1/chat/completions", "completion_window": "24h"},
            headers=_bearer(tenant_b["key"]),
        )
        assert cross.status_code == 422, cross.text
        assert absent.status_code == 422, absent.text
        assert cross.json()["code"] == "ERR_BATCH_INPUT_FILE_INVALID"
        assert absent.json()["code"] == cross.json()["code"]  # uniform — no enumeration oracle

    async def test_batches_ambiguous_both_or_neither(
        self, client: Any, tenant_a: dict[str, str]
    ) -> None:
        """R:batch_ambiguous — exactly one of {line_items, input_file_id} must be present."""
        up = await _upload(
            client, tenant_a["key"], filename="in.jsonl", data=_JSONL,
            purpose="batch", content_type="application/jsonl",
        )
        both = await client.post(
            "/v1/batches",
            json={
                "input_file_id": up.json()["id"],
                "line_items": [{"custom_id": "c1", "body": {"model": "m", "messages": [{}]}}],
                "endpoint": "/v1/chat/completions",
                "completion_window": "24h",
            },
            headers=_bearer(tenant_a["key"]),
        )
        neither = await client.post(
            "/v1/batches",
            json={"endpoint": "/v1/chat/completions", "completion_window": "24h"},
            headers=_bearer(tenant_a["key"]),
        )
        assert both.status_code == 422, both.text
        assert neither.status_code == 422, neither.text
        assert both.json()["code"] == "ERR_BATCH_INPUT_AMBIGUOUS"
        assert neither.json()["code"] == "ERR_BATCH_INPUT_AMBIGUOUS"

    async def test_existing_line_items_path_unchanged(
        self, client: Any, tenant_a: dict[str, str]
    ) -> None:
        """Regression: the frozen batch-job-store line_items path stays byte-identical
        (input_file_id is an ADDITIVE alternative, not a replacement)."""
        resp = await client.post(
            "/v1/batches",
            json={"line_items": [
                {"custom_id": "c1", "body": {"model": "gpt-x", "messages": [{"role": "user",
                                                                             "content": "hi"}]}}
            ]},
            headers=_bearer(tenant_a["key"]),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["item_count"] == 1
