GATEWAY := apps/gateway

.PHONY: install lint typecheck allowlist test ci

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

ci: lint typecheck allowlist test
	@echo "✅ pipeline green"
