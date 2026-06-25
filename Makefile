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
	edge edge-up edge-sync edge-dashboard edge-smoke edge-down edge-logs edge-ps

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
	  tests/azure_audio \
	  tests/web_search

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
