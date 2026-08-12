# syntax=docker/dockerfile:1.7
# AI Proxy Gateway — uv-based production image
# Contract §3 (edge-envoy TASK.md): uv-based, non-root user (uid 1000), port 8000
# CMD: uvicorn gateway.main:create_app --factory (factory pattern for DI wiring)

# DIGEST-PINNED, AND PINNED TO THE EXACT PATCH RELEASE CI VALIDATES (todo #98).
#
# This image used to build `FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim` — a moving
# tag whose bundled interpreter was whatever uv shipped that week (3.12.12 at the time of
# writing), while `.python-version` and ci.yml both pin 3.12.13. So production ran an
# interpreter no gate had ever executed.
#
# That is not cosmetic here. `core/egress_policy.py` decides SSRF verdicts from `ipaddress`
# predicates whose values CHANGED inside the 3.12 series: `is_reserved` on
# `::ffff:10.20.30.40` is True on 3.12.3 and False on 3.12.4+ (CVE-2024-4032 / gh-113171).
# A security control that can reach different answers from the same tagged commit,
# depending only on when the image was built, defeats the "build from the release tag"
# reproducibility claim that the whole release process rests on.
#
# Why the OFFICIAL python image rather than uv's convenience image: uv can only provision
# interpreters that python-build-standalone publishes, and 3.12.13 is not among them — its
# newest 3.12 is 3.12.12. `uv python install` fails outright ("No download found for
# request: cpython-3.12.13-..."). The official image has the exact release, so uv is copied
# in as a static binary and the base supplies the interpreter. The alternative — aligning
# CI and dev DOWN to 3.12.12 — was rejected: the point is that the artifact matches what
# was tested, and matching upward loses nothing.
#
# Re-pin deliberately, and re-pin BOTH: `docker buildx imagetools inspect <tag>`, confirm a
# multi-arch LIST carrying linux/amd64, and keep PYTHON_IMAGE's tag equal to
# .python-version — the RUN assertion in the runtime stage below fails the build if it
# drifts. Cadence tracked with the compose pins (todo #115).
ARG PYTHON_IMAGE=python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.9.30@sha256:538e0b39736e7feae937a65983e49d2ab75e1559d35041f9878b7b7e51de91e4

# uv as a static binary, not as a base image — this stage exists only to be copied from.
FROM ${UV_IMAGE} AS uvbin

# ── Build stage: install dependencies (no dev deps) ───────────────────────────
FROM ${PYTHON_IMAGE} AS builder

COPY --from=uvbin /uv /usr/local/bin/uv

# only-system forbids uv from silently downloading a DIFFERENT interpreter when it dislikes
# the one present. That silent substitution is precisely the drift being closed here, so it
# must be an error rather than a fallback.
ENV UV_PYTHON_PREFERENCE=only-system \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Copy dependency manifests first for layer-cache efficiency.
# .python-version comes too — it is the single source of the interpreter pin, shared with
# ci.yml and guarded by tests/migrations/test_ci_workflow_parity.py.
COPY pyproject.toml uv.lock .python-version ./

# Install production deps only, frozen (deterministic, no resolution at build time)
# --no-install-project: only install deps, not the package itself (installed in runtime stage)
RUN uv sync --frozen --no-dev --no-install-project

# ── Runtime stage ──────────────────────────────────────────────────────────────
# Same pinned base as the builder — /app/.venv/bin/python is a SYMLINK to the base image's
# interpreter, so a different base here would leave the copied venv dangling.
FROM ${PYTHON_IMAGE} AS runtime

COPY --from=uvbin /uv /usr/local/bin/uv

ENV UV_PYTHON_PREFERENCE=only-system \
    UV_PYTHON_DOWNLOADS=never

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
COPY pyproject.toml uv.lock .python-version ./

# Install the project package itself into the existing venv (no deps re-install)
# This runs as root (before USER switch) so the venv remains consistent.
RUN uv sync --frozen --no-dev

# FAIL THE BUILD on interpreter drift — asserted against the venv that actually SHIPS.
#
# Ordering, stated honestly: uv fails closed BEFORE this line. Given a mismatched base, the
# builder's `uv sync` already aborts, because only-system + downloads=never leaves it no
# interpreter matching `.python-version` (verified: base 3.12.12 against a 3.12.13 pin dies
# at `uv sync`, not here). So this is a BACKSTOP, not the first line of defence.
#
# It is kept anyway because uv's refusal is uv's policy, not ours: it depends on this uv
# version's `.python-version` handling and on those two env vars surviving future edits. A
# `--python 3.12` added to the sync command, or a loosened default, would quietly restore
# the drift. This line states the invariant in OUR terms and evaluates it against the final
# artifact. Its logic is red-checked against a tampered pin.
#
# Loud on purpose: the failure being closed is a SILENT one. An image running the wrong
# patch keeps working and keeps passing its health check, while `core/egress_policy.py`
# quietly changes its mind about whether `::ffff:10.20.30.40` is reserved (CVE-2024-4032 /
# gh-113171 moved that answer inside the 3.12 series). A wrong SSRF verdict does not
# announce itself; a failed build does.
#
# `.python-version` is read at build time rather than restated here, so the Dockerfile does
# not become a second, silently-divergent declaration of the pin.
RUN PINNED="$(tr -d '[:space:]' < .python-version)" && \
    ACTUAL="$(/app/.venv/bin/python -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')" && \
    if [ "$ACTUAL" != "$PINNED" ]; then \
        echo "FATAL: image interpreter is $ACTUAL but .python-version pins $PINNED." >&2; \
        echo "The image would run a different interpreter than CI validated." >&2; \
        exit 1; \
    fi && \
    echo "interpreter pinned and verified: $ACTUAL"

# Fix ownership so the gateway user can read the venv and source
RUN chown -R gateway:gateway /app

# Venv bin on PATH; VIRTUAL_ENV tells uv not to create a new one
ENV PATH="/app/.venv/bin:$PATH"
ENV VIRTUAL_ENV="/app/.venv"

# Production environment — GATEWAY_JWT_SECRET must be provided at runtime
ENV GATEWAY_ENVIRONMENT=production

# Timezone is DECLARED, not inherited (todo #11). Measured in this image before the pin:
# TZ was unset and time.tzname was ('UTC','UTC') — i.e. UTC by ACCIDENT, because
# python:*-slim-bookworm ships no /etc/localtime and the C library falls back to UTC. A base-image
# change, or a node that mounts /etc/localtime in, would move every naive datetime to local time
# with no error and no failing test. Defence in depth rather than a bug fix: src currently has
# zero naive clock reads, and tests/repo_hygiene/test_utc_is_not_implicit.py keeps both halves
# true — this pin, and the absence of anything that would depend on it.
ENV TZ=UTC

EXPOSE 8000

USER gateway

# factory mode: create_app() is called by uvicorn; Settings loaded from env vars
# Use python from the venv directly — avoids uv re-sync at runtime
CMD ["python", "-m", "uvicorn", "gateway.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
