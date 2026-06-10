GATEWAY := apps/gateway

.PHONY: install lint typecheck allowlist allowlist-node test migrate migrate-check ci

install:
	cd $(GATEWAY) && uv sync

lint:
	cd $(GATEWAY) && uv run ruff check . && uv run ruff format --check .

typecheck:
	cd $(GATEWAY) && uv run mypy

allowlist:
	python3 scripts/check_allowlist.py

allowlist-node:
	python3 scripts/check_node_deps.py

test:
	cd $(GATEWAY) && uv run pytest

migrate:
	cd $(GATEWAY) && uv run alembic upgrade head

migrate-check:
	cd $(GATEWAY) && uv run alembic check

ci: lint typecheck allowlist allowlist-node test
	@echo "✅ pipeline green"
