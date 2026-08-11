GATEWAY := apps/gateway
DASHBOARD := apps/dashboard

# --- Full Docker + Envoy edge stack (production-shaped local run) ---
COMPOSE_E2E := infra/docker-compose.e2e.yml
E2E_GATEWAY := hydroa-e2e-gateway-1
EDGE_URL    ?= http://127.0.0.1:8080
# Shared HS256 secret for the gateway (signs JWTs) AND envoy (validates /admin/*).
# Override at will: `make edge GATEWAY_JWT_SECRET=...`
GATEWAY_JWT_SECRET ?= e2e-test-secret-change-me
# Inject the real provider key from apps/gateway/.env into compose interpolation
# WITHOUT exporting/printing it — only the vars the compose references reach a
# container (GATEWAY_OPENROUTER_API_KEY, GATEWAY_JWT_SECRET). Omitted if absent.
_ENVFILE := $(GATEWAY)/.env
_ENVFLAG := $(if $(wildcard $(_ENVFILE)),--env-file $(_ENVFILE),)

.PHONY: install lint typecheck allowlist allowlist-node test test-fast migrate migrate-check migrate-parity ci ci-e2e \
	e2e-edge edge edge-up edge-sync edge-dashboard edge-smoke edge-down edge-logs edge-ps \
	kind-preflight kind-load kind-up kind-wait kind-diag kind-smoke kind-e2e kind-e2e-ui kind-down

install:
	cd $(GATEWAY) && uv sync

lint:
	cd $(GATEWAY) && uv run ruff check . && uv run ruff format --check .

typecheck:
	cd $(GATEWAY) && uv run pyright

allowlist:
	python3 scripts/check_allowlist.py

allowlist-node:
	python3 scripts/check_node_deps.py

test:
	cd $(GATEWAY) && uv run pytest

# The suite as CI runs it. Same tests as `test`, same strictness — fanned across
# xdist workers sized to the runner instead of run serially.
#
# WHY THIS EXISTS: serial `make test` needs >74 min on `ubuntu-latest` (measured
# 2026-08-07, run 31197251730 — cancelled at 74m17s still running, vs ~37 min on a
# 12-core dev host). A gate that cannot finish reports `cancelled` and proves
# nothing, which is the whole fault R6 exists to close.
#
# `-n 4` matches ubuntu-latest's core count and is stated explicitly rather than
# `-n auto`, which would resolve to the HOST's core count — the hazard the
# `test-parallel` comment below already names (Redis logical dbs only map 1..12 in
# tests/_redis_env.py). A fixed 4 behaves identically on a runner and on any dev
# machine, which is what makes a CI gate reproducible.
#
# NO `--reruns`, deliberately, unlike `test-parallel`. An auto-retrying gate is a
# gate that hides the flake tail (todos #74/#79/#89), and R6's whole product is a
# check whose green MEANS something. If 4-way contention surfaces flakes, that is
# information to act on, not noise to suppress.
#
# `make ci` uses THIS target, so CI runs exactly `make ci` and nothing divergent
# (the release-integrity anchor); guarded by
# tests/migrations/test_ci_workflow_parity.py::test_ci_enforces_every_make_ci_gate.
test-ci:
	cd $(GATEWAY) && uv run pytest -n 4 --dist loadscope

# ONE shard of the CI suite — what each `gateway` matrix job in ci.yml runs.
#
# WHY: `test-ci` on a single ubuntu-latest took 65-82 min, and `-n 4` bought almost nothing
# over serial because that runner's 4 vCPUs are shared with the Postgres+Redis service
# containers while every worker carries a ~1.92x coverage multiplier and all workers contend
# on ONE database (measured, todo #96). More workers on one box is a dead end; more BOXES is
# the lever, and each matrix shard is its own runner with its own Postgres and Redis.
#
# `-n 4 --dist loadscope` is kept BYTE-IDENTICAL to test-ci on purpose: sharding is the only
# variable being changed, so if the suite behaves differently the cause is unambiguous.
#
# Splitting is FILE-level and balanced by test count (tests/_shard.py). Coverage is written
# per-shard and combined by `coverage-combine`, so the 80% gate still applies to the WHOLE
# suite — `--cov-fail-under=0` here defeats only the PER-SHARD check, which would otherwise
# fail every shard for not covering the other five sixths.
#
# PYTHONPATH=. is required: `-p tests._shard` is imported during pytest's preparse, before
# rootdir lands on sys.path.
SHARD  ?= 1
SHARDS ?= 6
test-ci-shard:
	cd $(GATEWAY) && PYTHONPATH=. PYTEST_SHARD=$(SHARD) PYTEST_SHARDS=$(SHARDS) \
	  COVERAGE_FILE=.coverage.shard$(SHARD) \
	  uv run pytest -n 4 --dist loadscope -p tests._shard \
	    --cov-report= --cov-fail-under=0

# Combine the per-shard coverage data and enforce the real 80% gate across the whole suite.
# Fails loudly when no shard data is present, because an empty combine would otherwise
# "pass" the coverage gate having measured nothing — the masked-gate failure mode.
coverage-combine:
	cd $(GATEWAY) && \
	  ls .coverage.shard* >/dev/null 2>&1 || { echo "❌ no .coverage.shard* files — nothing to combine"; exit 1; }
	cd $(GATEWAY) && uv run coverage combine .coverage.shard*
	cd $(GATEWAY) && uv run coverage report --fail-under=80

# Parallel full suite — same tests as `test`, fanned across xdist workers.
# Isolation: tests/_redis_env.py gives each worker a private Postgres database
# (gateway_test_gwN, auto-created/dropped by conftest) + a private Redis logical db
# (workers use dbs 1..12, non-xdist keeps db 9), so the per-test drop_all/create_all and
# the autouse Redis clear never collide across workers. `--dist loadscope` keeps every
# test FILE on one worker (module-level state + single migration DB per worker). `-n 12`
# fits this 12-core host inside Redis's 16 logical dbs — do NOT switch to `-n auto` on a
# >15-core host without widening the db mapping in _redis_env.py. pytest-cov combines
# per-worker coverage automatically, so the --cov-fail-under gate still holds.
# `--reruns 1` auto-heals the small flake tail that 12-way contention on the shared
# Postgres/Redis exposes (audit/health timing + a realtime DDL deadlock — all green in
# isolation); a genuinely broken test still fails both attempts. The serial `make test`
# stays strict (no reruns) and remains the authoritative gate.
test-parallel:
	cd $(GATEWAY) && uv run pytest -n 12 --dist loadscope --reruns 1 --reruns-delay 2

# Fast per-change gate: no-DB blast-radius suites (translation + dispatch + provider).
# MockTransport / pure-unit — runs without Postgres/Redis, no coverage gating.
# GATEWAY_TEST_SKIP_INFRA_CHECK=1 (suite-infra-tripwire M4): this target is the ONE entry
# point that is meant to run with the dev stack down, so it opts out of the sessionstart
# infrastructure preflight in tests/conftest.py. Every other entry point — `make test`,
# `make test-parallel`, and a bare `uv run pytest` — stays guarded.
test-fast:
	cd $(GATEWAY) && GATEWAY_TEST_SKIP_INFRA_CHECK=1 uv run pytest -p no:cacheprovider --no-cov -q \
	  tests/tool_translation tests/response_format_translation \
	  tests/provider_chat_dispatch tests/anthropic_provider tests/gemini_provider \
	  tests/anthropic_tool_use tests/gemini_tool_use \
	  tests/anthropic_json_mode tests/gemini_json_mode \
	  tests/gemini_embed_tokens tests/nonchat_soft_budget_alert \
	  tests/azure_verify tests/byok_verify \
	  tests/azure_audio tests/tts_input_cap \
	  tests/audio_translations \
	  tests/web_search \
	  tests/gemini_multimodal \
	  tests/objectstore \
	  tests/realtime_relay

migrate:
	cd $(GATEWAY) && uv run alembic upgrade head

migrate-check:
	cd $(GATEWAY) && uv run alembic check

# Migration parity gate — does the migration chain build the schema the ORM declares?
#
# `alembic check` is only meaningful against a database at head, so this uses a FRESH
# scratch database. Never gateway_test: the test run leaves an unstamped create_all schema
# there, and checking against that compares the ORM to itself.
#
# This lives in the Makefile rather than as inline CI shell so that `make ci` and the CI
# job cannot drift — which is not hypothetical. It ran ONLY in CI until 2026-08-10, so a
# local `make ci` was a strict subset of the gate, and 16 unregistered ORM modules
# (24 tables) sat undetected behind that gap for months. A local green must mean the same
# thing as a CI green; MILESTONE release-integrity names that as an anchor.
PARITY_DB ?= gateway_parity
PARITY_ADMIN_URL ?= postgresql://gateway:gateway@localhost:5433/gateway_test
PARITY_DB_URL ?= postgresql+asyncpg://gateway:gateway@localhost:5433/$(PARITY_DB)
migrate-parity:
	psql "$(PARITY_ADMIN_URL)" -c "DROP DATABASE IF EXISTS $(PARITY_DB);" -c "CREATE DATABASE $(PARITY_DB);"
	GATEWAY_DATABASE_URL='$(PARITY_DB_URL)' $(MAKE) migrate
	GATEWAY_DATABASE_URL='$(PARITY_DB_URL)' $(MAKE) migrate-check

# Collation-lineage preflight — run BEFORE any deploy that reuses an existing volume or
# restores an existing dump. Exit 0 = OK, 1 = FAIL (remedy required), 2 = UNKNOWN
# ("could not check", never a pass). See docs/runbooks/pgvector-deploy.md.
#   make pg-preflight DATABASE_URL='postgresql://user:pass@host:5432/db'
pg-preflight:
	@test -n "$(DATABASE_URL)" || { echo "usage: make pg-preflight DATABASE_URL='postgresql://...'"; exit 2; }
	cd $(GATEWAY) && uv run python ../../scripts/pg_preflight.py --database-url '$(DATABASE_URL)'

ci: lint typecheck allowlist allowlist-node test-ci migrate-parity
	@echo "✅ pipeline green"

# === Full Docker + Envoy edge stack =========================================
# Quickstart:  make edge          # build + start + sync catalog
#              make edge-dashboard # (separate terminal) UI → edge
#              make edge-smoke     # prove the path end-to-end
#              make edge-down      # tear down

# Bring up the whole stack (postgres + redis + gateway image + envoy edge) and
# sync the model catalog. Envoy: http://127.0.0.1:8080 (admin :9901).
edge: edge-up edge-sync
	@echo "✅ edge ready — Envoy $(EDGE_URL) | next: 'make edge-dashboard' and/or 'make edge-smoke'"

# Build + start the containers, wait for all healthchecks.
edge-up:
	GATEWAY_JWT_SECRET='$(GATEWAY_JWT_SECRET)' docker compose $(_ENVFLAG) -f $(COMPOSE_E2E) up --build -d --wait
	@echo "✅ edge up — Envoy $(EDGE_URL) (admin http://127.0.0.1:9901)"

# Populate the model catalog from the provider (runs inside the gateway
# container; /internal/* is shielded at the edge by design).
edge-sync:
	docker exec $(E2E_GATEWAY) python -c "import urllib.request as u; print(u.urlopen(u.Request('http://localhost:8000/internal/catalog/sync', method='POST'), timeout=60).read().decode())"

# Run the dashboard (foreground) wired to the edge. GATEWAY_URL is server-side
# runtime env; uses the local Next binary (not npx). Log in with the e2e tenant.
edge-dashboard:
	cd $(DASHBOARD) && npm run build
	cd $(DASHBOARD) && GATEWAY_URL=$(EDGE_URL) NEXT_PUBLIC_APP_URL=http://localhost:3000 npm run start -- -p 3000

# End-to-end smoke through the edge (auth + real completion + cost tracking).
edge-smoke:
	EDGE_URL=$(EDGE_URL) bash scripts/edge_smoke.sh

# Tear down (add VOLUMES=1 to also drop the e2e DB/redis data).
edge-down:
	docker compose -f $(COMPOSE_E2E) down $(if $(VOLUMES),-v,)

edge-logs:
	docker compose -f $(COMPOSE_E2E) logs -f --tail=100

edge-ps:
	docker compose -f $(COMPOSE_E2E) ps

# === kind local Kubernetes (production-shaped stack, zero cloud creds) =======
# Quickstart:  make kind-up      # cluster + build/load both images + helm install + wait Ready
#              make kind-smoke   # prove the Envoy edge answers through TLS from the host
#              make kind-down    # delete the cluster
# CLOUD-READY, KIND-VALIDATED: the real-cloud apply is a documented runbook (ci-e2e-pipeline),
# never executed here — this harness only ever targets the local kind context.
KIND_CLUSTER     ?= ai-proxy
KIND_EDGE_PORT   ?= 8443
KIND_WAIT_TIMEOUT ?= 300s
KIND_DIR         := infra/kind
GW_IMG           := ai-proxy-gateway:kind-local
DASH_IMG         := ai-proxy-dashboard:kind-local

# Fail fast with a clear message if any required tool is missing (design-for-failure).
kind-preflight:
	@command -v kind    >/dev/null 2>&1 || { echo "❌ kind not on PATH (brew install kind)"; exit 1; }
	@command -v kubectl >/dev/null 2>&1 || { echo "❌ kubectl not on PATH"; exit 1; }
	@command -v helm    >/dev/null 2>&1 || { echo "❌ helm not on PATH"; exit 1; }
	@command -v openssl >/dev/null 2>&1 || { echo "❌ openssl not on PATH (needed for the self-signed edge cert)"; exit 1; }
	@docker info >/dev/null 2>&1        || { echo "❌ docker daemon not reachable — is Docker running?"; exit 1; }

# Build BOTH images and load them into the cluster (no registry push).
kind-load:
	docker build -t $(GW_IMG)   $(GATEWAY)
	docker build -t $(DASH_IMG) $(DASHBOARD)
	kind load docker-image $(GW_IMG)   --name $(KIND_CLUSTER)
	kind load docker-image $(DASH_IMG) --name $(KIND_CLUSTER)

# Reproducible, idempotent bring-up: ensure cluster -> build/load -> mint TLS -> apply test-only
# stub + edge NodePort -> helm upgrade --install with the kind overlay -> bounded wait Ready.
kind-up: kind-preflight
	@kind get clusters | grep -qx $(KIND_CLUSTER) \
	  || kind create cluster --name $(KIND_CLUSTER) --config $(KIND_DIR)/cluster.yaml
	@$(MAKE) --no-print-directory kind-load
	@# Self-signed TLS for the edge — the kubernetes.io/tls Secret the chart references. Test-only.
	@tmp=$$(mktemp -d); \
	  openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj "/CN=ai-proxy.local" \
	    -keyout $$tmp/tls.key -out $$tmp/tls.crt >/dev/null 2>&1; \
	  kubectl create secret tls ai-proxy-edge-tls --cert=$$tmp/tls.crt --key=$$tmp/tls.key \
	    --dry-run=client -o yaml | kubectl apply -f -; \
	  rm -rf $$tmp
	kubectl apply -f $(KIND_DIR)/upstream-stub.yaml
	kubectl apply -f $(KIND_DIR)/edge-nodeport.yaml
	helm upgrade --install ai-proxy charts/ai-proxy -f charts/ai-proxy/values-kind.yaml \
	  --timeout $(KIND_WAIT_TIMEOUT)
	@$(MAKE) --no-print-directory kind-wait
	@echo "✅ kind stack Ready — edge https://127.0.0.1:$(KIND_EDGE_PORT) | next: 'make kind-smoke'"

# Bounded per-workload wait; on ANY breach dump diagnostics and fail (never hang, never false-green).
kind-wait:
	@for d in gateway dashboard envoy upstream-stub; do \
	  echo "⏳ rollout deploy/ai-proxy-$$d"; \
	  kubectl rollout status deploy/ai-proxy-$$d --timeout=$(KIND_WAIT_TIMEOUT) \
	    || { $(MAKE) --no-print-directory kind-diag; exit 1; }; \
	done
	@for s in postgres redis minio; do \
	  echo "⏳ rollout statefulset/ai-proxy-$$s"; \
	  kubectl rollout status statefulset/ai-proxy-$$s --timeout=$(KIND_WAIT_TIMEOUT) \
	    || { $(MAKE) --no-print-directory kind-diag; exit 1; }; \
	done

# Diagnostics on a not-ready failure (Reject kind_stack_not_ready).
kind-diag:
	@echo "🔎 ===== kind diagnostics (stack not Ready) ====="
	-kubectl get pods -o wide
	-kubectl get events --sort-by=.lastTimestamp | tail -n 30
	-for p in $$(kubectl get pods -o name); do echo "--- $$p ---"; kubectl describe $$p | sed -n '/Events:/,$$p'; done

# Prove the Envoy edge answers through TLS from the host (self-signed -> -k). Non-000 = edge up.
kind-smoke:
	@code=$$(curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1:$(KIND_EDGE_PORT)/api/health 2>/dev/null || echo 000); \
	  if [ "$$code" != "000" ]; then echo "✅ edge reachable through TLS (HTTP $$code)"; \
	  else echo "❌ edge unreachable at https://127.0.0.1:$(KIND_EDGE_PORT)"; exit 1; fi

# Envoy edge e2e on the docker-compose stack (NOT kind): brings up infra/docker-compose.e2e.yml,
# runs `pytest -m e2e`, tears the stack down on exit. Covers TLS termination, bearer authz and
# /internal blocking — apps/gateway/tests/edge/{test_e2e_edge,test_e2e_tls,test_authz_bearer}.py,
# 1128 lines of security-relevant surface.
#
# This target exists because until 2026-08-11 that suite had NO automated home and no manual one
# either: the tests are marked `@pytest.mark.e2e`, which pyproject `addopts` deselects from every
# run (`-m 'not e2e and not kind_e2e'`), and scripts/e2e_edge.sh was invoked by NOTHING —
# `grep e2e_edge Makefile` returned empty, so even a human could not run it without already
# knowing the script path. Deliberately NOT wired into `make ci`: it needs the compose stack, which
# the CI gateway job does not have. Discoverable and runnable is the fix here; gated is a separate
# decision. See todo #108.
e2e-edge:
	./scripts/e2e_edge.sh

# Live core-flow e2e (v53 task 7): up (idempotent) → seed pricing → drive the edge →
# assert an accurate, non-zero usage+cost row. Leaves the cluster up; add --down to remove.
kind-e2e:
	./scripts/e2e_kind.sh

# Live browser UI e2e (v53 task 9): up (idempotent) → ensure Chromium → drive the dashboard UI
# through the edge (real login → real authed surface). Leaves the cluster up; add --down to remove.
kind-e2e-ui:
	./scripts/e2e_kind_ui.sh

# The whole pipeline locally (v53 task 10): ONE kind-up, then BOTH e2e suites against the live
# cluster (API then browser). This is what the kind-e2e CI workflow runs on a runner; it is also
# the proof surface for the milestone's e2e exit criterion when Actions billing blocks the runner.
# `kind-up` is a prerequisite so the cluster exists once; each script then runs with --no-up.
ci-e2e: kind-up
	./scripts/e2e_kind.sh --no-up
	./scripts/e2e_kind_ui.sh --no-up
	@echo "✅ ci-e2e: full kind pipeline green (API + UI e2e)"

# Idempotent teardown — success even if the cluster is already absent.
kind-down:
	-kind delete cluster --name $(KIND_CLUSTER)
	@echo "✅ kind cluster '$(KIND_CLUSTER)' removed"
