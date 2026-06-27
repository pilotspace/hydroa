# syntax=docker/dockerfile:1.7
# AI Proxy Gateway — uv-based production image
# Contract §3 (edge-envoy TASK.md): uv-based, non-root user (uid 1000), port 8000
# CMD: uvicorn gateway.main:create_app --factory (factory pattern for DI wiring)

ARG UV_IMAGE=ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# ── Build stage: install dependencies (no dev deps) ───────────────────────────
FROM ${UV_IMAGE} AS builder

WORKDIR /app

# Copy dependency manifests first for layer-cache efficiency
COPY pyproject.toml uv.lock ./

# Install production deps only, frozen (deterministic, no resolution at build time)
# --no-install-project: only install deps, not the package itself (installed in runtime stage)
RUN uv sync --frozen --no-dev --no-install-project

# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM ${UV_IMAGE} AS runtime

WORKDIR /app

# Non-root user (uid 1000) — production security requirement
RUN groupadd --gid 1000 gateway && \
    useradd --uid 1000 --gid gateway --shell /bin/sh --create-home gateway

# Copy the installed virtualenv from the build stage (root-owned in /app/.venv)
COPY --from=builder /app/.venv /app/.venv

# Copy application source
COPY src/ ./src/
# Migration assets — the gateway-pod `migrate` initContainer runs `alembic upgrade head`
# from /app (alembic.ini script_location = migrations). Without these the init cannot migrate.
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY pyproject.toml uv.lock ./

# Install the project package itself into the existing venv (no deps re-install)
# This runs as root (before USER switch) so the venv remains consistent.
RUN uv sync --frozen --no-dev

# Fix ownership so the gateway user can read the venv and source
RUN chown -R gateway:gateway /app

# Venv bin on PATH; VIRTUAL_ENV tells uv not to create a new one
ENV PATH="/app/.venv/bin:$PATH"
ENV VIRTUAL_ENV="/app/.venv"

# Production environment — GATEWAY_JWT_SECRET must be provided at runtime
ENV GATEWAY_ENVIRONMENT=production

EXPOSE 8000

USER gateway

# factory mode: create_app() is called by uvicorn; Settings loaded from env vars
# Use python from the venv directly — avoids uv re-sync at runtime
CMD ["python", "-m", "uvicorn", "gateway.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
