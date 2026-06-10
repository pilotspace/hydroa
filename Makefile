GATEWAY := apps/gateway

.PHONY: install lint typecheck allowlist test migrate migrate-check ci

install:
	cd $(GATEWAY) && uv sync

lint:
	cd $(GATEWAY) && uv run ruff check . && uv run ruff format --check .

typecheck:
	cd $(GATEWAY) && uv run mypy

allowlist:
	python3 scripts/check_allowlist.py

test:
	cd $(GATEWAY) && uv run pytest

migrate:
	cd $(GATEWAY) && uv run alembic upgrade head

migrate-check:
	cd $(GATEWAY) && uv run alembic check

ci: lint typecheck allowlist test
	@echo "✅ pipeline green"
