GATEWAY := apps/gateway

.PHONY: install lint typecheck allowlist allowlist-node test test-fast migrate migrate-check ci

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
	  tests/gemini_embed_tokens tests/nonchat_soft_budget_alert

migrate:
	cd $(GATEWAY) && uv run alembic upgrade head

migrate-check:
	cd $(GATEWAY) && uv run alembic check

ci: lint typecheck allowlist allowlist-node test
	@echo "✅ pipeline green"
