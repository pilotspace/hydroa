---
type: Spec
title: Experience
lens: experience
project: ai-proxy (Hydroa) — the multi-tenant LLM gateway
generated: { by: add/3.2.0, at: 2026-08-12 }
---
## Now

Two audiences on one dashboard: the tenant's own console (keys, usage, budgets, playground,
model management, RAG) and the superadmin platform console (tenant directory, billing,
audit). Next.js App Router + Tailwind v4 + shadcn/Radix, on the "Aurora"/"Airier" token layer.
Five-tier pricing (Free $0 · Starter $1 · Pro $20 · Team $99 · Enterprise custom).

## Decisions that bind

- **A user-facing feature gets the UDD design loop, never bare CRUD plus a table.** Name the IA,
  the primary object of the page, and the signature element at design time.
- **WCAG AA and keyboard navigability are acceptance criteria, not polish.** An axe pass with
  zero serious/critical findings, plus the authed capture harness
  (`apps/dashboard/e2e-review/capture.spec.ts`).
- **Unit tests cannot catch `next build`.** Run `next build` + `next start` + a real request
  before any dashboard merge; authed visual capture needs a production build.
- **Restyles are presentation-only, and proven so.** Use a `data-slot` marker and a refute-read
  before the gate; verify the computed style on a live render (`@theme inline` is unlayered and
  silently loses a token collision).
- **Fail closed in the UI too.** A role-gated surface is absent for every other role including a
  still-loading identity — never a component that renders null.
- **An error the caller cannot act on is a defect.** Say which subsystem failed and what to do
  next; never blame the user's data for our outage.

## Deltas
<!-- the inbox: `- [open · <date>] <lesson>` — fold upward into the sections above, then retag [folded] -->
