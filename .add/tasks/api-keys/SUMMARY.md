# SUMMARY — api-keys build span

task: api-keys
fork_base: 87353f13cd9fc382c4019d7ad8feec862d05dc7d
outcome: PASS

## Evidence

tests_keys: 20 passed in 3.02s (tests/keys/ -q --no-cov)
tests_regression: 14 passed in 1.36s (tests/tenants/ tests/test_health.py tests/test_config.py -q --no-cov)
lint: ruff check src/ — All checks passed; ruff format src/ — 15 files left unchanged
mypy: mypy src/ — Success: no issues found in 38 source files

## Residue

none

## Competency deltas

- [DDD] GLOSSARY amended: "stored as SHA-256 hash" for API keys only; argon2 retained for user passwords — high-entropy CSPRNG secrets make offline brute-force infeasible, avoiding 50-200ms argon2 latency on /internal/authz hot path
- [TDD] byte-identical authz failure contract drove AuthzUseCase to always compute hash even for unknown/revoked rows — content-length oracle prevented by test design
- [SDD] explicit key_id pre-generation at router call site (uuid7() in router, threaded through use case to repository) prevents "child row with unset parent id" class of bug
- [ADD] lowest-confidence flag surfaced GLOSSARY/contract inconsistency before any code written — freeze gate validated as correct cross-artifact consistency checkpoint

## Files changed

apps/gateway/src/gateway/main.py
apps/gateway/src/gateway/keys/__init__.py
apps/gateway/src/gateway/keys/domain/__init__.py
apps/gateway/src/gateway/keys/domain/entities.py
apps/gateway/src/gateway/keys/domain/errors.py
apps/gateway/src/gateway/keys/domain/ports.py
apps/gateway/src/gateway/keys/application/__init__.py
apps/gateway/src/gateway/keys/application/use_cases.py
apps/gateway/src/gateway/keys/infrastructure/__init__.py
apps/gateway/src/gateway/keys/infrastructure/orm.py
apps/gateway/src/gateway/keys/infrastructure/repository.py
apps/gateway/src/gateway/keys/infrastructure/sha256_hasher.py
apps/gateway/src/gateway/keys/api/__init__.py
apps/gateway/src/gateway/keys/api/schemas.py
apps/gateway/src/gateway/keys/api/deps.py
apps/gateway/src/gateway/keys/api/router.py
.add/tasks/api-keys/TASK.md
.add/tasks/api-keys/SUMMARY.md

## Auto-PASS conditions

- all tests/keys/ green (20/20)
- prior suites still green (14/14)
- ruff check src/ clean
- mypy src/ clean
- no test weakened or edited
- contract §3 not touched
- no security residue
- this run named as resolution owner (Claude Sonnet 4.6, auto mode, 2026-06-10)
