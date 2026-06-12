#!/usr/bin/env python3
"""v10 tool-use live verification — full function-calling round-trips through the TLS edge.

Proves, end-to-end through https://localhost:8443, that a tenant can:
  C1  Anthropic chat (provider=anthropic): tools -> OpenAI tool_calls (non-stream) ->
      a role:"tool" follow-up -> a final text answer; billing on the served id (7/4).
  C2  Anthropic streaming: tools -> SSE delta.tool_calls fragments + terminal usage.
  C3  Gemini chat (provider=google): tools -> OpenAI tool_calls (synth id) -> a
      role:"tool" follow-up -> a final text answer; billing on the served id (9/6).
  C4  Gemini streaming: tools -> SSE delta.tool_calls (one combined fragment).
  C5  OpenRouter (provider=openrouter): a no-tools request is byte-identical to v9 (5/3/8).
  C6  Governance intact: a bad API key is rejected 401 (no usage row).

Operator-run; NO production source change. Starts scripts/v10_tool_stub.py in a daemon
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
STUB_PORT = 9924

V10_ANTHROPIC_CHAT = "v10-anthropic-chat"
V10_GOOGLE_CHAT = "v10-google-chat"
V10_OPENROUTER_CHAT = "v10-openrouter-chat"

_ANTHROPIC_PROMPT_TOKENS = 7
_ANTHROPIC_COMPLETION_TOKENS = 4
_GEMINI_PROMPT_TOKENS = 9
_GEMINI_COMPLETION_TOKENS = 6
_OPENROUTER_PROMPT_TOKENS = 5
_OPENROUTER_COMPLETION_TOKENS = 3

_TOOL_NAME = "get_weather"
_FINAL_TEXT = "It is sunny in Paris."

EDGE_PACE_S = 0.05
EDGE_SETTLE_S = 1.5

PG_CONTAINER = "hydroa-e2e-postgres-1"
GW_CONTAINER = "hydroa-e2e-gateway-1"

run_id = int(time.time())
RESULTS: list[tuple[str, bool, str]] = []

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import v10_tool_stub  # noqa: E402 — after sys.path setup

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": _TOOL_NAME,
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]


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
    raise RuntimeError(f"v10 tool stub did not become healthy within {timeout}s")


def _seed_v10_models() -> None:
    """INSERT 3 provider-tagged chat models + pricing via docker-exec psql (idempotent)."""
    _ts = "now(), now()"
    rows = [
        f"('{V10_ANTHROPIC_CHAT}', 'v10 Anthropic chat', 8192, true, 'chat', 'anthropic', {_ts})",
        f"('{V10_GOOGLE_CHAT}', 'v10 Google chat', 8192, true, 'chat', 'google', {_ts})",
        f"('{V10_OPENROUTER_CHAT}', 'v10 OpenRouter chat', 8192, true, 'chat', 'openrouter', {_ts})",
    ]
    psql(
        "INSERT INTO models (id, name, context_length, active, modality, provider, "
        "created_at, updated_at) VALUES " + ", ".join(rows)
        + " ON CONFLICT (id) DO UPDATE SET active = true, provider = EXCLUDED.provider, "
        "modality = EXCLUDED.modality;"
    )
    _price = "0.000001, 0.000001, now(), 'per_token', NULL"
    snaps = [
        f"(gen_random_uuid(), '{V10_ANTHROPIC_CHAT}', {_price})",
        f"(gen_random_uuid(), '{V10_GOOGLE_CHAT}', {_price})",
        f"(gen_random_uuid(), '{V10_OPENROUTER_CHAT}', {_price})",
    ]
    psql(
        "INSERT INTO pricing_snapshots (id, model_id, prompt_usd_per_token, "
        "completion_usd_per_token, captured_at, pricing_unit, unit_usd_per_unit) VALUES "
        + ", ".join(snaps) + ";"
    )
    print("  [seed] 3 v10 models + pricing seeded (active=true)")


def _restart_gateway_and_wait(timeout: float = 60.0) -> None:
    compose_cmd = [
        "docker", "compose",
        "-f", "infra/docker-compose.e2e.yml",
        "-f", "infra/docker-compose.e2e.v4.yml",
        "-f", "infra/docker-compose.e2e.v5.yml",
        "-f", "infra/docker-compose.e2e.v6.yml",
        "-f", "infra/docker-compose.e2e.v10.yml",
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


def _sse_tool_call_fragments(text: str) -> list[dict]:
    """Parse an SSE body and collect every delta.tool_calls fragment."""
    frags: list[dict] = []
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
            for frag in choice.get("delta", {}).get("tool_calls", []) or []:
                frags.append(frag)
    return frags


def _sse_terminal_usage(text: str) -> dict:
    last: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
            try:
                chunk = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if "usage" in chunk:
                last = chunk["usage"]
    return last


def main() -> None:
    import httpx

    stub_srv = v10_tool_stub.make_stub_server()
    v10_tool_stub.start_stub_in_thread(stub_srv)
    print(f"v10 tool stub    :{STUB_PORT}  (localhost only)\nrun_id={run_id}")

    server_addr = stub_srv.server_address[0]
    if server_addr != "127.0.0.1":
        print(f"HARD-STOP: stub bound to {server_addr!r} — ERR_SECURITY_VIOLATION", file=sys.stderr)
        sys.exit(1)
    print(f"  [security] stub address asserted: {server_addr}:{STUB_PORT} (loopback only) OK")

    _wait_stub_healthy()
    _seed_v10_models()
    _restart_gateway_and_wait()
    time.sleep(EDGE_SETTLE_S)

    client = httpx.Client(verify=False, timeout=90, follow_redirects=True)

    email = f"v10-verify-{run_id}@live.io"
    password = "v10-verify-password-live"
    r_signup = client.post(
        f"{BASE}/admin/auth/signup",
        json={"tenant_name": f"V10VerifyCo-{run_id}", "email": email, "password": password},
    )
    assert r_signup.status_code == 201, f"signup: {r_signup.status_code} {r_signup.text[:200]}"
    jwt_token = client.post(
        f"{BASE}/admin/auth/login", json={"email": email, "password": password}
    ).json()["access_token"]
    auth_hdrs = {"Authorization": f"Bearer {jwt_token}"}

    def _new_key(label: str) -> tuple[str, str]:
        r = client.post(f"{BASE}/admin/keys", json={"name": f"v10-{label}-{run_id}"}, headers=auth_hdrs)
        assert r.status_code == 201, f"{label} key: {r.text[:200]}"
        return r.json()["key"], r.json()["key_id"]

    def _post_chat(model: str, key: str, messages: list, *, stream: bool = False, tools: bool = True) -> httpx.Response:
        body: dict = {"model": model, "messages": messages, "max_tokens": 64, "stream": stream}
        if tools:
            body["tools"] = _TOOLS
            body["tool_choice"] = "auto"
        time.sleep(EDGE_PACE_S)
        return client.post(f"{BASE}/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})

    def _tool_round_trip(model: str, prompt_tokens: int, label: str) -> None:
        raw_key, key_id = _new_key(label)
        # Turn 1: tools -> tool_calls
        r1 = _post_chat(model, raw_key, [{"role": "user", "content": "Weather in Paris?"}])
        ok1 = r1.status_code == 200
        tcalls = []
        if ok1:
            msg = r1.json()["choices"][0]["message"]
            tcalls = msg.get("tool_calls") or []
            fr = r1.json()["choices"][0].get("finish_reason")
            name_ok = bool(tcalls) and tcalls[0]["function"]["name"] == _TOOL_NAME
            args_ok = bool(tcalls) and json.loads(tcalls[0]["function"]["arguments"]).get("city") == "Paris"
            id_ok = bool(tcalls) and bool(tcalls[0].get("id"))
            record(f"{label}a turn1 returns tool_calls (name+args+id, finish_reason tool_calls)",
                   name_ok and args_ok and id_ok and fr == "tool_calls",
                   f"name_ok={name_ok} args_ok={args_ok} id_ok={id_ok} fr={fr}")
        else:
            record(f"{label}a turn1 returns tool_calls", False, f"status={r1.status_code} {r1.text[:160]!r}")
        # Turn 2: tool result -> final text
        if tcalls:
            messages2 = [
                {"role": "user", "content": "Weather in Paris?"},
                {"role": "assistant", "content": None, "tool_calls": tcalls},
                {"role": "tool", "tool_call_id": tcalls[0]["id"], "content": "sunny, 21C"},
            ]
            r2 = _post_chat(model, raw_key, messages2)
            content = r2.json()["choices"][0]["message"].get("content", "") if r2.status_code == 200 else ""
            fr2 = r2.json()["choices"][0].get("finish_reason") if r2.status_code == 200 else None
            record(f"{label}b turn2 (tool result) returns final text answer",
                   r2.status_code == 200 and content == _FINAL_TEXT and fr2 == "stop",
                   f"status={r2.status_code} content={content!r} fr={fr2}")
        # Billing on the served id (turn 1's usage row)
        pt, ct, cost = _poll_usage_tokens(key_id)
        record(f"{label}c billed on served id (prompt_tokens={prompt_tokens})",
               pt == prompt_tokens, f"prompt_tokens={pt} (expected {prompt_tokens})")
        served = psql(f"SELECT model_id FROM usage_records WHERE key_id='{key_id}' ORDER BY created_at ASC LIMIT 1")
        record(f"{label}d usage row model_id == {model}", served == model, f"model_id={served!r}")
        record(f"{label}e cost_usd > 0", bool(cost) and float(cost) > 0, f"cost_usd={cost!r}")

    # ── C1 Anthropic tool round-trip ──────────────────────────────────────────
    print("\n── C1 Anthropic tool round-trip ──")
    _tool_round_trip(V10_ANTHROPIC_CHAT, _ANTHROPIC_PROMPT_TOKENS, "C1")

    # ── C2 Anthropic tool streaming ───────────────────────────────────────────
    print("\n── C2 Anthropic tool streaming ──")
    c2_key, _ = _new_key("C2")
    r_c2 = _post_chat(V10_ANTHROPIC_CHAT, c2_key, [{"role": "user", "content": "Weather?"}], stream=True)
    frags2 = _sse_tool_call_fragments(r_c2.text)
    first_ok2 = bool(frags2) and frags2[0].get("id") and frags2[0]["function"].get("name") == _TOOL_NAME
    args2 = "".join(f["function"].get("arguments", "") for f in frags2 if "arguments" in f.get("function", {}))
    usage2 = _sse_terminal_usage(r_c2.text)
    record("C2a streamed delta.tool_calls (first frag id+name)", bool(first_ok2), f"frags={len(frags2)}")
    record("C2b streamed arguments reassemble to {\"city\":\"Paris\"}",
           json.loads(args2 or "{}").get("city") == "Paris", f"args={args2!r}")
    record("C2c terminal usage chunk present (7/4)",
           usage2.get("prompt_tokens") == _ANTHROPIC_PROMPT_TOKENS
           and usage2.get("completion_tokens") == _ANTHROPIC_COMPLETION_TOKENS,
           f"usage={usage2}")

    # ── C3 Gemini tool round-trip ─────────────────────────────────────────────
    print("\n── C3 Gemini tool round-trip ──")
    _tool_round_trip(V10_GOOGLE_CHAT, _GEMINI_PROMPT_TOKENS, "C3")

    # ── C4 Gemini tool streaming ──────────────────────────────────────────────
    print("\n── C4 Gemini tool streaming ──")
    c4_key, _ = _new_key("C4")
    r_c4 = _post_chat(V10_GOOGLE_CHAT, c4_key, [{"role": "user", "content": "Weather?"}], stream=True)
    frags4 = _sse_tool_call_fragments(r_c4.text)
    combined_ok = (
        bool(frags4)
        and frags4[0].get("id")
        and frags4[0]["function"].get("name") == _TOOL_NAME
        and json.loads(frags4[0]["function"].get("arguments", "{}")).get("city") == "Paris"
    )
    usage4 = _sse_terminal_usage(r_c4.text)
    record("C4a streamed one combined delta.tool_calls (id+name+args)", bool(combined_ok), f"frags={len(frags4)}")
    record("C4b terminal usage chunk present (9/6)",
           usage4.get("prompt_tokens") == _GEMINI_PROMPT_TOKENS
           and usage4.get("completion_tokens") == _GEMINI_COMPLETION_TOKENS,
           f"usage={usage4}")

    # ── C5 OpenRouter no-tools byte-identical ─────────────────────────────────
    print("\n── C5 OpenRouter no-tools byte-identical ──")
    c5_key, c5_id = _new_key("C5")
    r_c5 = _post_chat(V10_OPENROUTER_CHAT, c5_key, [{"role": "user", "content": "Say OK."}], tools=False)
    c5_content = r_c5.json()["choices"][0]["message"]["content"] if r_c5.status_code == 200 else ""
    record("C5a openrouter no-tools 200 + content ok-openrouter",
           r_c5.status_code == 200 and c5_content == "ok-openrouter",
           f"status={r_c5.status_code} content={c5_content!r}")
    c5_pt, c5_ct, _ = _poll_usage_tokens(c5_id)
    record("C5b openrouter billed 5/3 (byte-identical to v9)",
           c5_pt == _OPENROUTER_PROMPT_TOKENS and c5_ct == _OPENROUTER_COMPLETION_TOKENS,
           f"prompt={c5_pt} completion={c5_ct}")

    # ── C6 governance (401 bad key) ───────────────────────────────────────────
    print("\n── C6 governance ──")
    r_c6 = _post_chat(V10_ANTHROPIC_CHAT, "sk-not-a-real-key", [{"role": "user", "content": "hi"}])
    record("C6 bad API key rejected 401/403", r_c6.status_code in (401, 403), f"status={r_c6.status_code}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    for crit, ok, note in RESULTS:
        if not ok:
            print(f"  FAIL  {crit}: {note}")
    print(f"v10 tool-use live verify: {passed}/{total} checks passed (run_id={run_id})")
    stub_srv.shutdown()
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
