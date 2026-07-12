#!/usr/bin/env python3
"""vertex-adapter live verification — REAL GCP service-account credentials required.

Operator-run; NOT part of the automated pytest suite, NOT run by CI (mirrors
scripts/live_v20_verify.py / live_v21_verify.py's "separate manually-run script
exercises real credentials" convention, vertex-adapter TASK.md §2).

Exercises the REAL VertexTokenProvider (RFC 7523 JWT-bearer mint against the REAL
``https://oauth2.googleapis.com/token``) and VertexCompletionUpstream (a REAL
generateContent call to ``{europe-west4|asia-southeast1}-aiplatform.googleapis.com``)
in-process — no docker/edge stack required, since Vertex's auth is a plain OAuth2
bearer flow (no signed-request-per-call scheme like Bedrock's SigV4, no per-request URL
routing config like Azure's deployment map that benefits from exercising the live edge).

Usage:
    export GATEWAY_VERTEX_LIVE_SA_JSON=/path/to/service-account.json
    export GATEWAY_VERTEX_LIVE_MODEL=eu.gemini-2.5-flash   # optional, default below
    python3 scripts/live_vertex_verify.py

The service-account JSON must be a standard GCP key file with at least
``project_id``, ``client_email``, ``private_key`` fields, and the service account must
have the "Vertex AI User" role (or broader) on the target project, with the Vertex AI
API enabled.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPTS_DIR)
_GATEWAY_SRC = os.path.join(_REPO_ROOT, "apps", "gateway", "src")
if _GATEWAY_SRC not in sys.path:
    sys.path.insert(0, _GATEWAY_SRC)

DEFAULT_MODEL = "eu.gemini-2.5-flash"

RESULTS: list[tuple[str, bool, str]] = []


def record(criterion: str, ok: bool, note: str) -> None:
    RESULTS.append((criterion, ok, note))
    status = "PASS" if ok else "FAIL"
    print(f"{status}  {criterion}: {note}", flush=True)


async def main() -> None:
    sa_path = os.environ.get("GATEWAY_VERTEX_LIVE_SA_JSON", "")
    if not sa_path or not os.path.isfile(sa_path):
        print(
            "GATEWAY_VERTEX_LIVE_SA_JSON must point at a real GCP service-account JSON "
            "file. This script makes REAL Vertex AI calls and is never run by CI.",
            file=sys.stderr,
        )
        sys.exit(2)

    with open(sa_path, encoding="utf-8") as f:
        sa = json.load(f)

    model = os.environ.get("GATEWAY_VERTEX_LIVE_MODEL", DEFAULT_MODEL)

    from gateway.proxy.domain.credential_context import (
        reset_provider_credential,
        set_provider_credential,
    )
    from gateway.proxy.domain.provider_credentials import GoogleServiceAccountCredential
    from gateway.proxy.infrastructure.vertex_ad import VertexTokenProvider
    from gateway.proxy.infrastructure.vertex_upstream import VertexCompletionUpstream

    cred = GoogleServiceAccountCredential(
        project_id=sa["project_id"],
        client_email=sa["client_email"],
        private_key=sa["private_key"],
        private_key_id=sa.get("private_key_id"),
    )

    print(f"vertex-adapter live verify  project={cred.project_id}  model={model}")
    print("=" * 60)

    # ── C1: real JWT-bearer token mint against the real GCP token endpoint ──────
    print("\n── C1 TOKEN MINT ──")
    provider = VertexTokenProvider(config=cred.to_vertex_service_account_config())
    try:
        token = await provider.get_token()
        c1_ok = isinstance(token, str) and len(token) > 0
        record("C1 real RFC 7523 JWT-bearer mint succeeds", c1_ok, f"token_len={len(token)}")
    except Exception as exc:  # noqa: BLE001 — report, don't crash the script
        record("C1 real RFC 7523 JWT-bearer mint succeeds", False, f"error={exc}")
    finally:
        await provider.aclose()

    # ── C2: real chat completion against the real Vertex endpoint ──────────────
    print("\n── C2 REAL CHAT ──")
    upstream = VertexCompletionUpstream(default_max_tokens=64)
    tok = set_provider_credential(cred)
    try:
        status, body = await upstream.complete(
            {"model": model, "messages": [{"role": "user", "content": "Say OK."}]}
        )
        usage = body.get("usage", {}) if isinstance(body, dict) else {}
        c2_ok = status == 200 and body.get("object") == "chat.completion"
        record("C2 real Vertex generateContent → OpenAI chat.completion", c2_ok, f"status={status} usage={usage}")
    except Exception as exc:  # noqa: BLE001
        record("C2 real Vertex generateContent → OpenAI chat.completion", False, f"error={exc}")
    finally:
        reset_provider_credential(tok)

    # ── C3: real streaming completion ───────────────────────────────────────────
    print("\n── C3 REAL STREAM ──")
    tok = set_provider_credential(cred)
    try:
        chunks: list[bytes] = []
        async for chunk in upstream.stream(
            {
                "model": model,
                "messages": [{"role": "user", "content": "Count to three."}],
                "stream": True,
            }
        ):
            chunks.append(chunk)
        text = b"".join(chunks).decode("utf-8", errors="replace")
        c3_ok = "chat.completion.chunk" in text and "[DONE]" in text
        record("C3 real Vertex streamGenerateContent → OpenAI SSE + [DONE]", c3_ok, f"bytes={len(text)}")
    except Exception as exc:  # noqa: BLE001
        record("C3 real Vertex streamGenerateContent → OpenAI SSE + [DONE]", False, f"error={exc}")
    finally:
        reset_provider_credential(tok)

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    for crit, ok, note in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'} {crit}")
    print(f"\nvertex-adapter live verify: {passed}/{total} checks passed")
    if passed != total:
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
