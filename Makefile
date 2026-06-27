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

.PHONY: install lint typecheck allowlist allowlist-node test test-fast migrate migrate-check ci \
	edge edge-up edge-sync edge-dashboard edge-smoke edge-down edge-logs edge-ps \
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

# Fast per-change gate: no-DB blast-radius suites (translation + dispatch + provider).
# MockTransport / pure-unit — runs without Postgres/Redis, no coverage gating.
test-fast:
	cd $(GATEWAY) && uv run pytest -p no:cacheprovider --no-cov -q \
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

ci: lint typecheck allowlist allowlist-node test
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

# Live core-flow e2e (v53 task 7): up (idempotent) → seed pricing → drive the edge →
# assert an accurate, non-zero usage+cost row. Leaves the cluster up; add --down to remove.
kind-e2e:
	./scripts/e2e_kind.sh

# Live browser UI e2e (v53 task 9): up (idempotent) → ensure Chromium → drive the dashboard UI
# through the edge (real login → real authed surface). Leaves the cluster up; add --down to remove.
kind-e2e-ui:
	./scripts/e2e_kind_ui.sh

# Idempotent teardown — success even if the cluster is already absent.
kind-down:
	-kind delete cluster --name $(KIND_CLUSTER)
	@echo "✅ kind cluster '$(KIND_CLUSTER)' removed"
