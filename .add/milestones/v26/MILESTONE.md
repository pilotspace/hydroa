# MILESTONE: UI↔BE coverage — close the dashboard gaps   ⟦ROADMAP STUB — NOT YET OPENED⟧

goal: every implemented backend control-plane capability has a dashboard surface — close the 6 UI↔BE gaps the v25 intake audit surfaced.

rationale: new-major follow-on (v26; project-lead/auto, 2026-06-16, Tin confirmed "full coverage program" at v25 intake). This is a **roadmap stub written to disk for visibility only** — it is intentionally NOT registered as an active engine milestone (a milestone is only ever active or archived; a not-yet-opened one is a planning doc). **Open it with `add.py new-milestone v26 --force` after v25 closes**, then re-fill from this sketch through the normal scope-drafting loop (co-specify + human confirm). The provider-config gap is delivered by v25, so it is excluded here.

stage: production · status: PLANNED (stub) · drafted: 2026-06-16

> This is a SKETCH, not a frozen milestone. Task slugs, dependencies, and exit
> criteria below are a starting point — re-validate them at open time.

## Source — the v25 intake UI↔BE gap audit (file-cited)
Backend capabilities that had NO dashboard surface as of 2026-06-16 (provider-config → delivered by v25):

| Gap | Backend today | UI today |
|---|---|---|
| Alert events viewer | `alert_events` table + webhook dispatcher; NO `/admin/alerts` endpoint | ✗ |
| Routing config **write** | `/admin/routing` is read-only; model-groups/strategy/limits are env/startup only | ✗ |
| Catalog sync trigger | `POST /internal/catalog/sync` exists | ✗ (no button) |
| SSO login button | full OIDC flow wired (`/auth/oidc/login` + callback) | ✗ (`/login` is email+password only) |
| Upstream health view | `UpstreamHealthChecker` writes events; NO read endpoint | ✗ |
| Rate-limit counter visibility | per-key rpm/tpm enforced in Redis | ~ editable, not observable |

## Tasks (breadth-first SKETCH — re-validate at open)
- [ ] alerts-events-viewer       depends-on: none — new `GET /admin/alerts` (read `alert_events`, tenant-scoped, paginated) + dashboard Alerts page (history, type, dedupe_key, delivery status).
- [ ] sso-login-button           depends-on: none — `/login` "Sign in with SSO" entry (domain field → `/auth/oidc/login`); BE flow already exists (UI-only, lightest slice — likely the first one to land).
- [ ] catalog-sync-trigger       depends-on: none — expose catalog re-sync to owners: `POST /admin/catalog/sync` (tenant-safe wrapper over the internal sync) + a dashboard button on `/models` with last-sync timestamp.
- [ ] upstream-health-view       depends-on: alerts-events-viewer — `GET /admin/health/upstreams` (last ping per provider/up-down) + a health panel (compose with the alerts viewer).
- [ ] ratelimit-counter-view     depends-on: none — `GET /admin/ratelimits` (current Redis rpm/tpm consumption per key) + a read-only panel on `/keys` or `/usage`.
- [ ] routing-config-write       depends-on: none — the largest slice: write endpoints for model-groups / routing strategy / per-deployment rpm-tpm limits + circuit/retry thresholds (today env-only), and a `/routing` editor. May warrant its own sub-milestone — re-size at open.

## Exit criteria (SKETCH; each maps to a task)
- [ ] An owner browses alert history (soft-budget, circuit-open, health) in the dashboard.   (← alerts-events-viewer)
- [ ] A tenant with SSO configured logs in from the `/login` page without a manual URL.   (← sso-login-button)
- [ ] An owner forces a catalog re-sync from the dashboard and sees the new last-sync time.   (← catalog-sync-trigger)
- [ ] An owner sees per-provider upstream up/down status in the dashboard.   (← upstream-health-view)
- [ ] An owner sees current rpm/tpm consumption per key.   (← ratelimit-counter-view)
- [ ] An owner edits model-groups / routing strategy / deployment limits from the dashboard.   (← routing-config-write)

## Status: PLANNED (roadmap stub) — open after v25 closes via `add.py new-milestone v26 --force`.
