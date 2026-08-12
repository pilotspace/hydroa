# Shared context — v8-live-verify (v8 task 5/5, milestone-closing live verify)

Frozen task spec: `.add/tasks/v8-live-verify/TASK.md` (§1–§4; §3 CONTRACT FROZEN @ v1).
Read it FIRST and in full — single source of truth. Do NOT edit it.

## What this task is
PURE HARNESS task — NO gateway source or test change. It proves the whole v8 router surface LIVE
through the TLS edge, twice. Three files only:
  1. `scripts/v8_router_stub.py`        — multi-deployment OpenRouter chat stub on 127.0.0.1:9922
  2. `infra/docker-compose.e2e.v8.yml`  — additive overlay (gateway env only)
  3. `scripts/live_v8_verify.py`         — the C1–C7 executable check list; exit 0 iff all pass

The v8 router (routing-strategy + balance-strategies + deployment-limits) is ALREADY built and
wired in main.py. You add NOTHING to src/. `git show --stat` at the end MUST list ONLY those 3 files.

## Hard rules (NON-NEGOTIABLE — violation = HARD-STOP / ERR_FROZEN_VIOLATION)
- Stub binds 127.0.0.1 ONLY — NEVER 0.0.0.0 (security; the verify asserts it before any check).
- NO real key read/logged/echoed/committed. The v6 overlay's GATEWAY_OPENROUTER_API_KEY
  ="stub-openrouter-key" is a NON-SECRET placeholder — keep it; never empty it (an empty bearer
  fails client-side — the v7 lesson). Never cat/echo/print any .env file.
- Redis keys + logs carry deployment_id only — never key strings/secrets.
- Do NOT touch any gateway src/ file or any test. Do NOT edit the v6 stub/overlay/verify (frozen).
- Do NOT make any frozen seam async or edit a frozen contract.

## Templates to MIRROR (read these — copy the idiom, change the content)
- `scripts/v6_fault_stub.py` — the stub structure: STUB_HOST/PORT constants, _fault_table +
  _call_counters with locks, set_fault/get_behavior/increment_and_get_count, BaseHTTPRequestHandler
  do_POST (/__faults + /api/v1/chat/completions), make_stub_server(), start_stub_in_thread().
  ADD a GET /__counters handler (do_GET) returning the per-model counter dict as JSON — v6 lacks it.
  Use a NEW port 9922 (v6 uses 9920). Behaviors needed: "ok" (default), "fail_5xx", {"fail_n":K}.
- `scripts/live_v6_verify.py` — the verify structure: BASE=https://localhost:8443, run_id, record(),
  psql() via `docker exec hydroa-e2e-postgres-1 psql -U gateway -d gateway_e2e -tA -c`, redis via
  `docker exec hydroa-e2e-redis-1 redis-cli`, _set_fault() (POST stub /__faults), _wait_gateway_healthy,
  _reset_gateway_state (docker restart gateway + redis-cli del cooldown keys), _catalog_sync, seed via
  raw SQL, _poll_candidate_state, the tenant-signup → key-create → chat → poll-usage_records flow,
  main() printing "ALL CRITERIA PASS (n/n)" and sys.exit(0/1). The stub is auto-started in a daemon
  thread by the verify script (start_stub_in_thread). Containers: hydroa-e2e-{postgres,gateway,redis}-1.
- `infra/docker-compose.e2e.v6.yml` — the overlay header-comment + `services: gateway: environment:`
  shape. v8 overlay composes ADDITIVELY on base+v4+v5+v6; sets only the keys in §3 (overrides
  GATEWAY_OPENROUTER_BASE_URL → :9922, adds GATEWAY_ROUTING_STRATEGY + GATEWAY_MODEL_GROUPS; keeps
  the v6 cooldown/retry/api-key values by composing on top of the v6 overlay).

## Config facts (from src/gateway/core/config.py — do NOT change config.py)
- GATEWAY_MODEL_GROUPS: JSON dict alias→ordered member list; member = bare string (→ weight-1, no
  limits) OR object {"model_id","weight","tpm_limit","rpm_limit"}. validation_alias accepts
  GATEWAY_MODEL_GROUPS / model_groups.
- GATEWAY_ROUTING_STRATEGY ∈ {ordered, simple-shuffle, least-busy, latency}; default "ordered".
- limit_gate is wired in main.py ONLY when ≥1 configured deployment has rpm_limit or tpm_limit.
- Redis deplimit key the gate READS (src/gateway/proxy/infrastructure/redis_limit_gate.py):
    gateway:deplimit:rpm:{deployment_id}:{bucket}   where bucket = floor(time.time()/60)  (window_s=60)
  is_saturated returns True when the GET'd count >= rpm_limit. So to force saturation, SET that key
  to the limit value for the CURRENT bucket (and the NEXT bucket, for minute-boundary safety).

## The v8 model groups (FROZEN — copy verbatim into the overlay)
  v8-dist  : [{model_id:stub/dep-a,weight:1},{model_id:stub/dep-b,weight:3}]
  v8-limit : [{model_id:stub/lim-a,rpm_limit:5},{model_id:stub/lim-b}]
  v8-allsat: [{model_id:stub/sat-a,rpm_limit:3},{model_id:stub/sat-b,rpm_limit:3}]
  v8-fb    : ["stub/fb-primary","stub/fb-secondary"]
  v8-bill  : [{model_id:stub/bill-a,weight:1},{model_id:stub/bill-b,weight:1}]
The 8 distinct stub model ids must each be SEEDED into the catalog (provider='openrouter',
active=true) + a pricing_snapshot (per_token, non-zero) by the verify script, AFTER _catalog_sync
(which deactivates non-upstream models). Look at how live_v6/v7_verify.py seed model rows + pricing.

## The C1–C7 checks (full detail in §3/§4 of TASK.md — implement exactly)
- C1 distribution: ≥40 chats to v8-dist → /__counters dep-a>0 ∧ dep-b>0 ∧ dep-b>dep-a.
- C2 limit skip: seed lim-a rpm window=5 → chats to v8-limit all 200 by lim-b, lim-a counter flat.
- C3 all saturated: seed sat-a & sat-b windows=3 → chat v8-allsat → 429 ERR_RATE_LIMITED +
  Retry-After, no counter bump, no usage row (poll confirms 0).
- C4 fallback: fault fb-primary fail_5xx → chat v8-fb → 200 by fb-secondary, exactly 1 usage row on it.
- C5 cooldown: drive ≥2 fb-primary failures → fb-primary cooled (skipped); after TTL(5s)+fault clear,
  eligible again. (Reuse v6 cooldown idiom; threshold=2, ttl=5 from the kept v6 knobs.)
- C6 billing served id: chat v8-bill → exactly 1 usage row, model_id = SERVED id (match /__counters),
  never the alias "v8-bill", cost_usd>0.
- C7 governance+TLS: chat v8-dist with NO bearer → 401, 0 usage rows; all via https://localhost:8443.

## How to run the live stack (the orchestrator runs the authoritative double-pass; you must make it runnable)
  docker compose -f infra/docker-compose.e2e.yml -f infra/docker-compose.e2e.v4.yml \
    -f infra/docker-compose.e2e.v5.yml -f infra/docker-compose.e2e.v6.yml \
    -f infra/docker-compose.e2e.v8.yml up --build -d --wait
  uv run --project apps/gateway python scripts/live_v8_verify.py     # twice; both exit 0

## Deliverables
- The 3 files above, runnable. A short note of the exact compose + run commands.
- Do NOT git commit (orchestrator commits after review + live run). Do NOT edit TASK.md.
