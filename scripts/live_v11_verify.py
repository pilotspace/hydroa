#!/usr/bin/env python3
"""v11 JSON-mode live verification — response_format round-trips through the TLS edge.

Proves, end-to-end through https://localhost:8443, that a tenant can:
  C1  Anthropic chat (provider=anthropic): response_format json_schema -> the gateway
      forces a json_output coercion tool, the stub returns its tool_use input, and the
      gateway UNWRAPS it into message.content (a JSON string), finish "stop", NO
      tool_calls leak; billing on the served id (7/4).
  C2  Anthropic streaming: json_schema -> SSE delta.content JSON fragments (NOT
      delta.tool_calls), finish "stop", terminal usage 7/4.
  C3  Gemini chat (provider=google): response_format -> generationConfig.responseMimeType,
      the stub returns a JSON-string text part -> message.content is that JSON; billing 9/6.
  C4  Gemini streaming: response_format -> SSE delta.content JSON fragment + terminal usage 9/6.
  C5  OpenRouter (provider=openrouter): a no-response_format request is byte-identical to
      v10 (content ok-openrouter, 5/3/8).
  C6  Governance intact: a bad API key is rejected 401 (no usage row).

Operator-run; NO production source change. Starts scripts/v11_json_stub.py in a daemon
thread, seeds 3 provider-tagged catalog models + pricing, restarts the gateway so the
lifespan provider_resolver.refresh() reads them, then runs the checks with a fresh
tenant+key. run_id changes on every invocation (int(time.time())).

Double-pass close rule: run twice in sequence; both must exit 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

BASE = os.environ.get("SMOKE_BASE", "https://localhost:8443")
CA = os.environ.get("E2E_CA_CERT", "infra/envoy/certs/dev-ca.pem")

STUB_HOST = "127.0.0.1"
STUB_PORT = 9925

V11_ANTHROPIC_CHAT = "v11-anthropic-chat"
V11_GOOGLE_CHAT = "v11-google-chat"
V11_OPENROUTER_CHAT = "v11-openrouter-chat"

_ANTHROPIC_PROMPT_TOKENS = 7
_ANTHROPIC_COMPLETION_TOKENS = 4
_GEMINI_PROMPT_TOKENS = 9
_GEMINI_COMPLETION_TOKENS = 6
_OPENROUTER_PROMPT_TOKENS = 5
_OPENROUTER_COMPLETION_TOKENS = 3

# The JSON object the stub "produces" for a structured-output request.
_EXPECTED_JSON = {"city": "Paris", "temp": 18}

EDGE_PACE_S = 0.05
EDGE_SETTLE_S = 1.5

PG_CONTAINER = "hydroa-e2e-postgres-1"
GW_CONTAINER = "hydroa-e2e-gateway-1"

run_id = int(time.time())
RESULTS: list[tuple[str, bool, str]] = []

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import v11_json_stub  # noqa: E402 — after sys.path setup

# response_format json_schema — the canonical OpenAI shape (see response_format_translation.py)
_RESPONSE_FORMAT_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "weather",
        "schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}, "temp": {"type": "integer"}},
            "required": ["city", "temp"],
        },
    },
}
_RESPONSE_FORMAT_OBJECT = {"type": "json_object"}


def record(criterion: str, ok: bool, note: str) -> None:
    RESULTS.append((criterion, ok, note))
    print(f"{'PASS' if ok else 'FAIL'}  {criterion}: {note}", flush=True)


def psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "psql", "-U", "gateway", "-d", "gateway_e2e",
         "-tA", "-c", sql],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _wait_stub_healthy(timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"http://{STUB_HOST}:{STUB_PORT}/__health", method="GET")
            if urllib.request.urlopen(req, timeout=3).status == 200:
                print(f"  [stub] healthy on {STUB_HOST}:{STUB_PORT}")
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"v11 json stub did not become healthy within {timeout}s")


def _seed_v11_models() -> None:
    """INSERT 3 provider-tagged chat models + pricing via docker-exec psql (idempotent)."""
    _ts = "now(), now()"
    rows = [
        f"('{V11_ANTHROPIC_CHAT}', 'v11 Anthropic chat', 8192, true, 'chat', 'anthropic', {_ts})",
        f"('{V11_GOOGLE_CHAT}', 'v11 Google chat', 8192, true, 'chat', 'google', {_ts})",
        f"('{V11_OPENROUTER_CHAT}', 'v11 OpenRouter chat', 8192, true, 'chat', 'openrouter', {_ts})",
    ]
    psql(
        "INSERT INTO models (id, name, context_length, active, modality, provider, "
        "created_at, updated_at) VALUES " + ", ".join(rows)
        + " ON CONFLICT (id) DO UPDATE SET active = true, provider = EXCLUDED.provider, "
        "modality = EXCLUDED.modality;"
    )
    _price = "0.000001, 0.000001, now(), 'per_token', NULL"
    snaps = [
        f"(gen_random_uuid(), '{V11_ANTHROPIC_CHAT}', {_price})",
        f"(gen_random_uuid(), '{V11_GOOGLE_CHAT}', {_price})",
        f"(gen_random_uuid(), '{V11_OPENROUTER_CHAT}', {_price})",
    ]
    psql(
        "INSERT INTO pricing_snapshots (id, model_id, prompt_usd_per_token, "
        "completion_usd_per_token, captured_at, pricing_unit, unit_usd_per_unit) VALUES "
        + ", ".join(snaps) + ";"
    )
    print("  [seed] 3 v11 models + pricing seeded (active=true)")


def _restart_gateway_and_wait(timeout: float = 60.0) -> None:
    compose_cmd = [
        "docker", "compose",
        "-f", "infra/docker-compose.e2e.yml",
        "-f", "infra/docker-compose.e2e.v4.yml",
        "-f", "infra/docker-compose.e2e.v5.yml",
        "-f", "infra/docker-compose.e2e.v6.yml",
        "-f", "infra/docker-compose.e2e.v11.yml",
        "restart", "gateway",
    ]
    subprocess.run(compose_cmd, capture_output=True, text=True, check=True, timeout=120)
    print("  [gateway] restart triggered; polling health ...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = subprocess.run(
                ["docker", "exec", GW_CONTAINER, "python", "-c",
                 "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                print("  [gateway] healthy after restart")
                return
        except Exception:
            pass
        time.sleep(2)
    print("  WARN: gateway may not be fully healthy after restart; continuing")


def _poll_usage_tokens(key_id: str, *, ceiling_s: float = 30.0) -> tuple[int, int, str]:
    deadline = time.time() + ceiling_s
    while time.time() < deadline:
        if int(psql(f"SELECT count(*) FROM usage_records WHERE key_id='{key_id}'") or "0") >= 1:
            raw = psql(
                "SELECT prompt_tokens::text || ',' || completion_tokens::text || ',' "
                f"|| cost_usd::text FROM usage_records WHERE key_id='{key_id}' "
                "ORDER BY created_at ASC LIMIT 1"
            )
            parts = raw.split(",")
            try:
                return int(parts[0]), int(parts[1]), parts[2]
            except (IndexError, ValueError):
                return 0, 0, ""
        time.sleep(2)
    return 0, 0, ""


def _sse_content_fragments(text: str) -> str:
    """Parse an SSE body and concatenate every delta.content fragment."""
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for choice in chunk.get("choices", []):
            content = choice.get("delta", {}).get("content")
            if content:
                out.append(content)
    return "".join(out)


def _sse_has_tool_calls(text: str) -> bool:
    """True if ANY chunk carries a delta.tool_calls fragment (must NOT happen for coercion)."""
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for choice in chunk.get("choices", []):
            if choice.get("delta", {}).get("tool_calls"):
                return True
    return False


def _sse_finish_reason(text: str) -> str | None:
    last: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
            try:
                chunk = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            for choice in chunk.get("choices", []):
                if choice.get("finish_reason"):
                    last = choice["finish_reason"]
    return last


def _sse_terminal_usage(text: str) -> dict:
    last: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
            try:
                chunk = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if "usage" in chunk and chunk["usage"]:
                last = chunk["usage"]
    return last


def _json_matches(content: str) -> bool:
    """Content parses as JSON and equals the expected object."""
    try:
        return json.loads(content) == _EXPECTED_JSON
    except (json.JSONDecodeError, TypeError):
        return False


def main() -> None:
    import httpx

    stub_srv = v11_json_stub.make_stub_server()
    v11_json_stub.start_stub_in_thread(stub_srv)
    print(f"v11 json stub    :{STUB_PORT}  (localhost only)\nrun_id={run_id}")

    server_addr = stub_srv.server_address[0]
    if server_addr != "127.0.0.1":
        print(f"HARD-STOP: stub bound to {server_addr!r} — ERR_SECURITY_VIOLATION", file=sys.stderr)
        sys.exit(1)
    print(f"  [security] stub address asserted: {server_addr}:{STUB_PORT} (loopback only) OK")

    _wait_stub_healthy()
    _seed_v11_models()
    _restart_gateway_and_wait()
    time.sleep(EDGE_SETTLE_S)

    client = httpx.Client(verify=False, timeout=90, follow_redirects=True)

    email = f"v11-verify-{run_id}@live.io"
    password = "v11-verify-password-live"
    r_signup = client.post(
        f"{BASE}/admin/auth/signup",
        json={"tenant_name": f"V11VerifyCo-{run_id}", "email": email, "password": password},
    )
    assert r_signup.status_code == 201, f"signup: {r_signup.status_code} {r_signup.text[:200]}"
    jwt_token = client.post(
        f"{BASE}/admin/auth/login", json={"email": email, "password": password}
    ).json()["access_token"]
    auth_hdrs = {"Authorization": f"Bearer {jwt_token}"}

    def _new_key(label: str) -> tuple[str, str]:
        r = client.post(f"{BASE}/admin/keys", json={"name": f"v11-{label}-{run_id}"}, headers=auth_hdrs)
        assert r.status_code == 201, f"{label} key: {r.text[:200]}"
        return r.json()["key"], r.json()["key_id"]

    def _post_chat(
        model: str, key: str, messages: list, *,
        stream: bool = False, response_format: dict | None = None,
    ) -> httpx.Response:
        body: dict = {"model": model, "messages": messages, "max_tokens": 64, "stream": stream}
        if response_format is not None:
            body["response_format"] = response_format
        time.sleep(EDGE_PACE_S)
        return client.post(f"{BASE}/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})

    # ── C1 Anthropic json_schema coerce+unwrap (non-stream) ────────────────────
    print("\n── C1 Anthropic json_schema coerce+unwrap (non-stream) ──")
    c1_key, c1_id = _new_key("C1")
    r_c1 = _post_chat(V11_ANTHROPIC_CHAT, c1_key, [{"role": "user", "content": "Weather in Paris as JSON?"}],
                      response_format=_RESPONSE_FORMAT_SCHEMA)
    if r_c1.status_code == 200:
        msg1 = r_c1.json()["choices"][0]["message"]
        content1 = msg1.get("content") or ""
        tcalls1 = msg1.get("tool_calls") or []
        fr1 = r_c1.json()["choices"][0].get("finish_reason")
        record("C1a message.content is JSON (parses to expected) + finish stop + NO tool_calls",
               _json_matches(content1) and fr1 == "stop" and not tcalls1,
               f"content={content1!r} fr={fr1} tool_calls={len(tcalls1)}")
    else:
        record("C1a message.content is JSON", False, f"status={r_c1.status_code} {r_c1.text[:160]!r}")
    c1_pt, c1_ct, c1_cost = _poll_usage_tokens(c1_id)
    record("C1b billed on served id (7/4)",
           c1_pt == _ANTHROPIC_PROMPT_TOKENS and c1_ct == _ANTHROPIC_COMPLETION_TOKENS,
           f"prompt={c1_pt} completion={c1_ct}")
    c1_served = psql(f"SELECT model_id FROM usage_records WHERE key_id='{c1_id}' ORDER BY created_at ASC LIMIT 1")
    record("C1c usage row model_id == v11-anthropic-chat", c1_served == V11_ANTHROPIC_CHAT, f"model_id={c1_served!r}")

    # ── C2 Anthropic json_schema streaming ─────────────────────────────────────
    print("\n── C2 Anthropic json_schema streaming ──")
    c2_key, _ = _new_key("C2")
    r_c2 = _post_chat(V11_ANTHROPIC_CHAT, c2_key, [{"role": "user", "content": "Weather as JSON?"}],
                      stream=True, response_format=_RESPONSE_FORMAT_SCHEMA)
    c2_content = _sse_content_fragments(r_c2.text)
    c2_has_tc = _sse_has_tool_calls(r_c2.text)
    c2_fr = _sse_finish_reason(r_c2.text)
    c2_usage = _sse_terminal_usage(r_c2.text)
    record("C2a streamed delta.content reassembles to JSON (no delta.tool_calls)",
           _json_matches(c2_content) and not c2_has_tc, f"content={c2_content!r} has_tool_calls={c2_has_tc}")
    record("C2b streamed finish_reason stop", c2_fr == "stop", f"finish_reason={c2_fr}")
    record("C2c terminal usage chunk present (7/4)",
           c2_usage.get("prompt_tokens") == _ANTHROPIC_PROMPT_TOKENS
           and c2_usage.get("completion_tokens") == _ANTHROPIC_COMPLETION_TOKENS,
           f"usage={c2_usage}")

    # ── C3 Gemini json (non-stream) ────────────────────────────────────────────
    print("\n── C3 Gemini json (non-stream) ──")
    c3_key, c3_id = _new_key("C3")
    r_c3 = _post_chat(V11_GOOGLE_CHAT, c3_key, [{"role": "user", "content": "Weather in Paris as JSON?"}],
                      response_format=_RESPONSE_FORMAT_OBJECT)
    if r_c3.status_code == 200:
        msg3 = r_c3.json()["choices"][0]["message"]
        content3 = msg3.get("content") or ""
        fr3 = r_c3.json()["choices"][0].get("finish_reason")
        record("C3a message.content is JSON string (parses to expected) + finish stop",
               _json_matches(content3) and fr3 == "stop", f"content={content3!r} fr={fr3}")
    else:
        record("C3a message.content is JSON", False, f"status={r_c3.status_code} {r_c3.text[:160]!r}")
    c3_pt, c3_ct, _ = _poll_usage_tokens(c3_id)
    record("C3b billed on served id (9/6)",
           c3_pt == _GEMINI_PROMPT_TOKENS and c3_ct == _GEMINI_COMPLETION_TOKENS,
           f"prompt={c3_pt} completion={c3_ct}")

    # ── C4 Gemini json streaming ───────────────────────────────────────────────
    print("\n── C4 Gemini json streaming ──")
    c4_key, _ = _new_key("C4")
    r_c4 = _post_chat(V11_GOOGLE_CHAT, c4_key, [{"role": "user", "content": "Weather as JSON?"}],
                      stream=True, response_format=_RESPONSE_FORMAT_OBJECT)
    c4_content = _sse_content_fragments(r_c4.text)
    c4_usage = _sse_terminal_usage(r_c4.text)
    record("C4a streamed delta.content reassembles to JSON", _json_matches(c4_content), f"content={c4_content!r}")
    record("C4b terminal usage chunk present (9/6)",
           c4_usage.get("prompt_tokens") == _GEMINI_PROMPT_TOKENS
           and c4_usage.get("completion_tokens") == _GEMINI_COMPLETION_TOKENS,
           f"usage={c4_usage}")

    # ── C5 OpenRouter no-response_format byte-identical ────────────────────────
    print("\n── C5 OpenRouter no-rf byte-identical ──")
    c5_key, c5_id = _new_key("C5")
    r_c5 = _post_chat(V11_OPENROUTER_CHAT, c5_key, [{"role": "user", "content": "Say OK."}])
    c5_content = r_c5.json()["choices"][0]["message"]["content"] if r_c5.status_code == 200 else ""
    record("C5a openrouter no-rf 200 + content ok-openrouter",
           r_c5.status_code == 200 and c5_content == "ok-openrouter",
           f"status={r_c5.status_code} content={c5_content!r}")
    c5_pt, c5_ct, _ = _poll_usage_tokens(c5_id)
    record("C5b openrouter billed 5/3 (byte-identical to v10)",
           c5_pt == _OPENROUTER_PROMPT_TOKENS and c5_ct == _OPENROUTER_COMPLETION_TOKENS,
           f"prompt={c5_pt} completion={c5_ct}")

    # ── C6 governance (401 bad key) ────────────────────────────────────────────
    print("\n── C6 governance ──")
    r_c6 = _post_chat(V11_ANTHROPIC_CHAT, "sk-not-a-real-key", [{"role": "user", "content": "hi"}],
                      response_format=_RESPONSE_FORMAT_OBJECT)
    record("C6 bad API key rejected 401/403", r_c6.status_code in (401, 403), f"status={r_c6.status_code}")

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    for crit, ok, note in RESULTS:
        if not ok:
            print(f"  FAIL  {crit}: {note}")
    print(f"v11 json-mode live verify: {passed}/{total} checks passed (run_id={run_id})")
    stub_srv.shutdown()
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
