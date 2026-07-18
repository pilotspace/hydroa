# MILESTONE: Commercial Self Serve

goal: A tenant can activate and transact with Hydroa entirely self-serve — the signup→first-call→invite→agent-approval→upgrade journey completes with zero platform-operator intervention.
rationale: new-major (Tin-confirmed 2026-07-17: "kick off new milestone then implement all enhancement of it in parallel"). Source: the code-grounded UX research P0 set (artifact 2026-07-17) — the commercial/activation layer is the professionalization gap while the data plane is already enterprise-grade. Relationship: EXTENDS account-tiers-billing (makes the shipped 5-tier plan catalog purchasable) · EXTENDS team-member-invite (adds email delivery to the shipped copy-link invites) · EXTENDS agent-gateway-v1 (gives the shipped RFC 8628 device flow its missing human approval surface) · does NOT overlap enterprise-domain-onboarding (domain-claims console stays there).
stage: production · status: active · created: 2026-07-17T14:32:21+00:00
release: pending

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Milestone grounding
- Touches : apps/gateway (tenants signup/plan, billing/credits, agent_oauth, core/config, new email + payments seams) · apps/dashboard (signup form, keys page, overview, docs, /activate route, plan/credits pages, invite dialog, BFF validation)
- Context : UX-research P0 findings (activation dead-ends at #coming-soon; credits page says "contact your platform operator"; device flow has no approval page; verification_uri defaults empty; invites are copy-link only; account_type never sent by the form)
- Honors  : no outbound IO without timeout + bounded retry + circuit breaker (PROJECT.md invariant) · append-only usage/billing ledger · every tenant-owned row tenant-scoped · plaintext secrets shown once · fire-and-forget ancillary IO never blocks the primary request (audit-writer precedent) · frozen invite copy-link contract stays byte-identical
- Anchors : `tenants/api/schemas.py` SignupRequest.account_type (Literal, already accepted) · `tenants/api/plan_router.py` GET /admin/plan (read-only today) · `platform_plans_router.py` PUT plan / credits topup (superadmin-only today) · `agent_oauth/api/device_approval_router.py` approve/deny (session-JWT-authed, complete) · `core/config.py` agent_oauth_verification_uri (default "") · `invites_router.py` POST /admin/invites (returns token; copy-link UI) · `credits/` ledger · components.toml gateway+dashboard green bars

## Scope
In:  self-serve checkout seam (plan upgrade · credit top-up; seat change DEFERRED at the checkout freeze — `CheckoutIntent.intent_type` reserves the variant additively) behind a PaymentProvider port with a dev/manual adapter default and a config-gated Stripe-shaped adapter; signup account_type wiring (personal→Free auto-plan) end-to-end; activation quickstart (post-key-create snippet panel with base URL + curl/SDK, Overview onboarding checklist for empty tenants, /docs quickstart page); /activate device-approval page + verification_uri default; transactional-email seam (EmailSender port, SMTP adapter, console fallback) wired to invites first.
Out: real Stripe production keys / live PSP onboarding (adapter ships config-gated, dev adapter default) · seat-change checkout (deferred at freeze; additive intent variant reserved) · invoice PDF/CSV export · billing-owner-of-record UI (todo, next slice) · domain-claims console (owned by enterprise-domain-onboarding) · email delivery for alerts/invoices (seam ships; only invites wired this milestone) · pricing-page catalog sync · member-scoped logs · playground Code tab · nav role-truthing (P1 set, next milestone).

CORRECTION (grounding, 2026-07-17): the "EmptyUpstreamKeyError precedent" cited in the shared decisions below is STALE — that class was deleted 2026-06-17 (retire-empty-key-guard). The live boot-guard idiom is a `model_validator(mode="after")` raising ValueError (`_validate_otel_config` shape); every task's set-but-empty guard uses THAT shape. The set-but-empty ⇒ boot-error PRINCIPLE stands unchanged.
NOTE (freeze reconciliation): `dashboard_public_origin` (gateway, email links) · `agent_oauth_verification_uri` (gateway, static default + prod guard) · `NEXT_PUBLIC_API_BASE_URL` (dashboard client, quickstart display) are three deliberately INDEPENDENT settings — no cross-task coupling; a "derive verification_uri from dashboard_public_origin" refinement is an observe-phase delta.

UI/UX scope (precise): /activate = focused single-purpose approval screen (code entry → agent identity + scopes + budget review → approve/deny confirmation states), Airier tokens, WCAG-AA floor, keyboard-first; quickstart panel = post-create step-list with copy buttons and language tabs (curl · OpenAI SDK python/js), mono for code, honest "playground needs no key" note; Overview checklist = dismissible 4-step empty-tenant card (create key → first call → BYOK → invite) that reads state from real endpoints, never a static graphic; plan/credits pages gain primary CTAs ("Upgrade plan", "Add credits") opening a checkout dialog with explicit price/seat math before confirm. Signature element: the /activate approval card mirrors the InvoiceStatusSeal dated-header idiom (tabular-nums, visible state seal) translated to an authorization document.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **PaymentProvider port** (new glossary term): checkout is a gateway-owned seam — `create_checkout(intent) -> CheckoutSession` + `confirm(session_id) -> CheckoutResult`; adapters: `dev` (auto-succeeds, records everything, default ON so the flow is demonstrable without a PSP) and `stripe` (config-gated, absent key = disabled, empty-but-set key = boot error per the EmptyUpstreamKeyError precedent). All plan/credit mutations land through the SAME existing domain operations the superadmin path uses — never a parallel write path.
- **EmailSender port** (new glossary term): fire-and-forget like audit writes — an email failure NEVER fails the primary request; adapters: `smtp` (config-gated) and `console` (default, logs the rendered mail). Copy-link invite response stays byte-identical; email is additive delivery.
- **Checkout is audit-logged**: every self-serve plan/credit/seat mutation writes an actor-attributed audit event (existing audit seam).
- **Self-serve never widens privilege**: checkout endpoints are tenant-scoped (owner/billing_admin), reuse existing RBAC permissions; the superadmin endpoints remain untouched.
- **Device approval is a security surface**: /activate reuses the existing session-JWT approve/deny endpoints unchanged; the page adds no new auth path; user-code entry is rate-limited by the existing per-IP/per-user limiters.

## Shared / risky contracts (freeze these first)
- PaymentProvider port + checkout endpoint shapes -> owning task self-serve-checkout
- EmailSender port -> owning task transactional-email
- verification_uri default + /activate route contract -> owning task device-activate-page

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] activation-quickstart   depends-on: none — signup account_type→Free-plan wiring end-to-end + post-key-create quickstart panel + Overview onboarding checklist + /docs quickstart page   · gate PASS
- [x] transactional-email     depends-on: none — EmailSender port (smtp + console adapters) + invite email delivery (copy-link preserved)   · gate PASS
- [x] device-activate-page    depends-on: none — /activate approval page over the existing device-flow approve/deny + verification_uri sensible default   [sensitivity: security]   · gate PASS (dual adversarial verify)
- [x] self-serve-checkout     depends-on: none — PaymentProvider port (dev + stripe-shaped adapters) + tenant-scoped checkout endpoints (plan upgrade · credit top-up · seats) + plan/credits page CTAs   [sensitivity: security]   · gate PASS (triple adversarial verify)
- [x] self-serve-plans-catalog   depends-on: none (loop task, closes self-serve-checkout D6) — tenant-scoped GET /admin/plans self-serve catalog + live UpgradePlanDialog wiring   [sensitivity: data]   · gate PASS

## Exit criteria (observable; map each to the task that delivers it)
- [x] A dashboard signup can choose personal and lands on the seeded Free plan; after creating a key the user sees a copy-paste quickstart (base URL + working curl) and an empty tenant sees an onboarding checklist instead of zeros        (← activation-quickstart)
- [x] Creating an invite with SMTP configured delivers an email containing the accept link; without SMTP the console adapter logs it; the copy-link response is byte-identical to today        (← transactional-email)
- [x] A headless client's user code can be entered at /activate by a logged-in member, showing the requesting agent + scopes + budget before approve/deny; the device-authorize response carries a non-empty verification_uri by default        (← device-activate-page)
- [x] A tenant owner upgrades the plan and tops up credits from /app/plan and /app/credits through the checkout seam without any superadmin action, with the mutation audit-logged and the dev adapter default-on (seat change deferred at freeze)        (← self-serve-checkout + self-serve-plans-catalog: the checkout seam ships the mutation; the plans-catalog closing task feeds the live upgrade-target menu that makes the upgrade selectable end-to-end)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway (backend) : NEW `email/` bounded context (EmailSender port + console/SMTP adapters + fire-and-forget dispatch + invite template); NEW `payments/` bounded context (PaymentProvider port + dev/stripe adapters + CheckoutService + checkout_sessions table via migration b7e2c4a9f1d3, additive plans.self_serve/audience columns); extracted shared `tenants/application/plan_assignment.py:assign_plan`; NEW `tenants/application/self_serve_plans.py` + GET /admin/plans; additive `POST /oauth/device/preview` (uniform-404, per-user-rate-limited) + agent_oauth_verification_uri default & prod boot-guard; new Settings blocks (email_smtp_*, dashboard_public_origin, payment_*, agent_oauth_preview_rpm) with model_validator boot-guards; new Permission.BILLING_MANAGE.
- dashboard (frontend) : signup account_type control + BFF validation; QuickstartPanel + OnboardingChecklist + /docs quickstart; InviteMemberDialog delivery-channel line; /activate device-approval page (AuthorizationSeal + sanitizeNext open-redirect guard + LoginForm nextPath); checkout dialogs (UpgradePlanDialog/AddCreditsDialog) live-wired on /app/plan + /app/credits via lib/checkout.ts; public-api-base-url.ts.
- tooling / skill / book : untouched (only .add/ task+milestone state + this MILESTONE.md).

### Cross-task evidence   (one row per task)
- activation-quickstart   : gate=PASS · tests=42 FE green (7 files) · residue=none (independent verify EARNED, refute-read clean)
- transactional-email     : gate=PASS · tests=52 BE + 3 FE green · residue=none (SRE-reliability verify EARNED; SMTPException⊂OSError retry-predicate correct; fail-open proven; 2 observe notes)
- device-activate-page    : gate=PASS · tests=20 BE + 37 FE green · residue=none blocking (DUAL adversarial security verify, both EARNED/CLEAR — enumeration-oracle byte-identical, open-redirect guard wired, boot-guard fail-closed; 2 defense-in-depth notes)
- self-serve-checkout     : gate=PASS · tests=38 BE + 10 FE green · residue=non-blocking observe deltas (TRIPLE adversarial security verify, all EARNED/CLEAR/no-HARD-STOP — privilege boundary holds, cross-tenant 404, Decimal money-math, double-confirm atomic, no PII/secret exposure incl. verified-safe model_dump path; F1 idempotency-namespace + expiry-transition + stripe-adapter coverage + N1 stripe-create-idempotency + D6→closed by plans-catalog)
- self-serve-plans-catalog: gate=PASS · tests=4 BE + 2 FE green · residue=none (data-sensitivity single verify + orchestrator refute-read; tenant-scoped, fail-closed audience filter)

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which): EC1←activation-quickstart · EC2←transactional-email · EC3←device-activate-page · EC4←self-serve-checkout (mutation seam) + self-serve-plans-catalog (live upgrade-target menu). Full-bundle suite green: gateway ~4031 tests (5 cross-task-drift casualties found & fixed — 2 table-manifests + 3 prod-Settings boot-guard tests — the rest confirmed flakes/DB-name artifacts), dashboard 1505 green (13 pre-existing Airier design-token failures untouched by this bundle, fail on main).
- goal: "A tenant can activate and transact with Hydroa entirely self-serve — signup→first-call→invite→agent-approval→upgrade with zero platform-operator intervention." Proof line: a personal signup lands on Free, mints a key with a copy-paste quickstart, invites a teammate (email dispatched), approves a headless agent at /activate, and upgrades their plan + tops up credits from /app/plan · /app/credits through the dev checkout adapter — every leg shipped and gated, no superadmin action anywhere in the path.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
- [x] full gateway + dashboard suites green on the bundle (chunked -n 4/6 per the load-saturation lesson) — done: 5 cross-task-drift fixes applied + committed (ab23951); all residual failures confirmed pre-existing flakes / Airier debt / DB-name artifacts.
- [ ] open a PR from the Close ship-review; Tin reviews + merges (gateway CI 0-step block → local evidence + admin-merge precedent) — AWAITING TIN'S PR AUTHORIZATION (global rule: ask before push/PR).
- [ ] tag / publish / deploy  (human-run, per release.md)
