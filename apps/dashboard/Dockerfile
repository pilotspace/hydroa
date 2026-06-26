# syntax=docker/dockerfile:1.7
# AI Proxy Dashboard — Next.js standalone production image.
# Contract §3 (dashboard-chart TASK.md): next.config.ts output:'standalone' -> the runtime
# stage runs the minimal `node server.js` as a NON-ROOT user on PORT 3000. No dev deps, no
# source, no dev runtime. Build context = apps/dashboard.

ARG NODE_IMAGE=node:22-alpine

# ── deps + build ────────────────────────────────────────────────────────────────
FROM ${NODE_IMAGE} AS builder
WORKDIR /app

# Install deps first (lockfile-frozen, deterministic) for layer-cache efficiency.
COPY package.json package-lock.json ./
RUN npm ci

# Build the standalone server. `output: 'standalone'` emits .next/standalone (server.js +
# the traced node_modules subset) and .next/static (the hashed client assets).
COPY . .
RUN npm run build

# ── runtime ─────────────────────────────────────────────────────────────────────
FROM ${NODE_IMAGE} AS runtime
WORKDIR /app

ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# The node image ships a non-root `node` user (uid 1000). Copy ONLY the minimal standalone
# output, owned by that user. (No public/ dir exists today; add a COPY of it here if one is.)
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static

EXPOSE 3000
USER node

# server.js binds HOSTNAME:PORT from env — the standalone entrypoint.
CMD ["node", "server.js"]
