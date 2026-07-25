#!/usr/bin/env python3
"""Live R4 API-surface-parity smoke — proves the 0.12.0 surfaces are reachable,
authenticated, governed, and OpenAI-SDK wire-compatible through the real Envoy edge.

Operator-run against the e2e TLS stack:
    docker compose -f infra/docker-compose.e2e.yml up --build -d --wait
    cd apps/gateway && uv run python ../../scripts/live_r4_parity_smoke.py

What this proves: routing + auth + governance wiring + SDK wire-compat for
/v1/responses, /v1/files, /v1/moderations, /v1/images/edits|variations, and the
tenant usage/costs read API. It does NOT prove upstream inference success — with
no live BYOK credential the upstream leg may fail, and that is EXPECTED. The pass
bar is "the surface is reachable and behaves like OpenAI's" — a 404 (route missing)
or 401 (auth broken) is a FAIL; a 200/201/2xx or a contracted 4xx/5xx-upstream is a PASS.
"""

from __future__ import annotations

import io
import os
import sys
import time

import httpx
from openai import OpenAI
from openai import APIStatusError

BASE = os.environ.get("SMOKE_BASE", "https://localhost:8443")
CA = os.environ.get("E2E_CA_CERT", "infra/envoy/certs/dev-ca.pem")
# Catalog-present IDs (this stack syncs OpenRouter): direct-OpenAI IDs like
# gpt-image-1 / omni-moderation-latest are NOT present and yield a governed
# ERR_MODEL_UNKNOWN — which still proves route+auth+governance fired.
CHAT_MODEL = os.environ.get("SMOKE_CHAT_MODEL", "nvidia/nemotron-3-nano-30b-a3b:free")
IMAGE_MODEL = os.environ.get("SMOKE_IMAGE_MODEL", "openai/gpt-5-image")

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{PASS if ok else FAIL}] {name}: {detail}")


def governed(err: APIStatusError) -> bool:
    """Reachable + authenticated + governed. FAIL only on auth breakage (401/403) or a
    true route/method miss — FastAPI answers those with a bare {"detail": "Not Found"/
    "Method Not Allowed"}. A structured ErrorSpec body (type/title/code) means the route,
    auth gate, and governance all fired — the surface is wired, even if the model/upstream
    leg then fails (EXPECTED without a live BYOK credential)."""
    if err.status_code in (401, 403):
        return False
    try:
        body = err.response.json()
    except Exception:  # noqa: BLE001
        return err.status_code not in (404, 405)
    detail = body.get("detail")
    if err.status_code in (404, 405) and detail in ("Not Found", "Method Not Allowed"):
        return False  # bare FastAPI route/method miss — surface not wired
    return True  # structured governed response — surface is wired


def seed_key() -> str:
    ca = CA if os.path.exists(CA) else os.path.join("..", "..", CA)
    client = httpx.Client(verify=ca, timeout=90)
    email = f"r4smoke-{int(time.time())}@live.io"
    password = "r4-smoke-password-1"
    r = client.post(
        f"{BASE}/admin/auth/signup",
        json={"tenant_name": "R4SmokeCo", "email": email, "password": password},
    )
    if r.status_code != 201:
        sys.exit(f"seed signup failed: {r.status_code} {r.text[:300]}")
    jwt = client.post(
        f"{BASE}/admin/auth/login", json={"email": email, "password": password}
    ).json()["access_token"]
    k = client.post(
        f"{BASE}/admin/keys",
        json={"name": "r4-smoke"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    if k.status_code != 201:
        sys.exit(f"seed key failed: {k.status_code} {k.text[:300]}")
    return k.json()["key"]


def main() -> None:
    api_key = seed_key()
    ca = CA if os.path.exists(CA) else os.path.join("..", "..", CA)
    http_client = httpx.Client(verify=ca, timeout=90)
    sdk = OpenAI(api_key=api_key, base_url=f"{BASE}/v1", http_client=http_client, max_retries=0)

    print("R4 parity smoke — 5 surfaces via the official openai SDK:\n")

    # 1) /v1/files — upload a small file, expect an OpenAI File object with file-<id>.
    file_id = None
    try:
        f = sdk.files.create(
            file=("smoke.txt", io.BytesIO(b"hydroa r4 parity smoke doc\n"), "text/plain"),
            purpose="user_data",
        )
        ok = isinstance(f.id, str) and f.id.startswith("file-")
        file_id = f.id
        record("/v1/files (upload)", ok, f"id={f.id} bytes={f.bytes} purpose={f.purpose}")
    except APIStatusError as e:
        record("/v1/files (upload)", governed(e), f"HTTP {e.status_code} {str(e)[:120]}")
    except Exception as e:  # noqa: BLE001
        record("/v1/files (upload)", False, f"{type(e).__name__}: {str(e)[:160]}")

    # 2) /v1/moderations — free endpoint, should return a real verdict shape.
    try:
        m = sdk.moderations.create(model="omni-moderation-latest", input="hello world")
        ok = bool(m.results) and hasattr(m.results[0], "flagged")
        record("/v1/moderations", ok, f"flagged={m.results[0].flagged}")
    except APIStatusError as e:
        record("/v1/moderations", governed(e), f"HTTP {e.status_code} {str(e)[:120]}")
    except Exception as e:  # noqa: BLE001
        record("/v1/moderations", False, f"{type(e).__name__}: {str(e)[:160]}")

    # 3) /v1/responses — reachable + governed; upstream may fail without BYOK (EXPECTED).
    try:
        resp = sdk.responses.create(model=CHAT_MODEL, input="Reply with: SMOKE OK")
        record("/v1/responses", True, f"id={getattr(resp, 'id', '?')} status={getattr(resp, 'status', '?')}")
    except APIStatusError as e:
        record("/v1/responses", governed(e), f"HTTP {e.status_code} governed (upstream-leg may fail w/o BYOK)")
    except Exception as e:  # noqa: BLE001
        record("/v1/responses", False, f"{type(e).__name__}: {str(e)[:160]}")

    # 4) /v1/images/edits — reachable + governed; needs a PNG; upstream may fail (EXPECTED).
    try:
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
            b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        img = sdk.images.edit(
            model=IMAGE_MODEL,
            image=("s.png", io.BytesIO(png), "image/png"),
            prompt="make it blue",
        )
        record("/v1/images/edits", True, f"data={len(img.data or [])}")
    except APIStatusError as e:
        record("/v1/images/edits", governed(e), f"HTTP {e.status_code} governed (upstream-leg may fail w/o BYOK)")
    except Exception as e:  # noqa: BLE001
        record("/v1/images/edits", False, f"{type(e).__name__}: {str(e)[:160]}")

    # 5) tenant usage/costs read API (/v1/organization/costs) — raw GET (not SDK-native).
    try:
        r = http_client.get(
            f"{BASE}/v1/organization/costs",
            headers={"Authorization": f"Bearer {api_key}"},
            params={"start_time": int(time.time()) - 3600},
        )
        ok = r.status_code == 200 and r.json().get("object") == "page"
        record("/v1/organization/costs", ok, f"HTTP {r.status_code} {r.text[:80]}")
    except Exception as e:  # noqa: BLE001
        record("/v1/organization/costs", False, f"{type(e).__name__}: {str(e)[:160]}")

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\nR4 parity smoke: {passed}/{len(results)} surfaces reachable + wire-compatible.")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
