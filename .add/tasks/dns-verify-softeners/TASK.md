# TASK: Soften the DNS-TXT verify flow: auto-poll + auto-flip, verify-later/notify, not-the-DNS-owner hand-off, registrar deep-link, one-block copy

slug: dns-verify-softeners · created: 2026-07-19 · stage: production
milestone: domain-onboarding-softening
component: dashboard
sensitivity: architecture   <!-- presentation + a client poll of the frozen verify endpoint; no verification-semantics change (auto-join FROZEN, untouched). NOT mechanical (a real behavior/state-machine change), NOT security (semantics unchanged) — keeps the human verify gate. -->
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: tests   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/dashboard/components/settings/DomainClaimsSettings.tsx:DomainClaimsSettings` — the OWNER-only
  domain-claims console. Today: `useQuery<DomainClaimListResponse>({ queryKey: CLAIMS_KEY=["admin-domain-claims"],
  queryFn: () => bffGet(CLAIMS_PATH="/admin/domain-claims"), retry: false })` — **no `refetchInterval`,
  no auto-poll**. `verifyClaim` mutation POSTs `${CLAIMS_PATH}/${id}/verify`; seal flips to verified ONLY in
  `onSuccess` (frozen R2). `onError` keys off `bffCode(err)`: `ERR_DOMAIN_VERIFICATION_FAILED` → loud alert
  "DNS record absent…"; `ERR_DNS_LOOKUP_FAILED` → loud alert "DNS lookup failed…"; else `getErrorTitle`.
  `challenge` state holds the CREATE response; `data-slot="dns-challenge"` card renders 3 `ChallengeField`s
  (Record type/name/value) each with its OWN "Copy" button; card copy = "…click Verify now. Propagation can
  take a few minutes." Row "Verify now" button shown iff `claim.status==="pending" && sealState(claim)==="pending"`.
  `copyToClipboard(text)` = fire-and-forget `navigator.clipboard?.writeText`.
- `apps/dashboard/components/settings/DomainStatusSeal.tsx:sealState(claim)` — derives verified/pending/expired
  from `(status, expires_at)`. CONSUMED read-only here; task-2 (`member-verified-recognition`) owns extending it.
- `apps/dashboard/lib/bff-client` — `bffGet/bffPost/bffDelete`, `BffError` (`.problem.code`/`.problem.title`).

Backend (FROZEN — inherited, NOT touched by this task):
- `apps/gateway/src/gateway/domain_capture/api/domain_claims_router.py:verify_domain_claim` — `POST
  /admin/domain-claims/{id}/verify` is the ONLY DNS re-check (the GET list never re-checks DNS → auto-poll
  MUST call verify, not just refetch). Rate-limited `domain_claim_verify_rpm=30`/min per tenant
  (`core/config.py:1406`). Verify error taxonomy (`core/error_catalog.py:727+`):
  `200`→verified · `400 ERR_DOMAIN_VERIFICATION_FAILED` (TXT absent/mismatch = **not-yet-propagated**) ·
  `503 ERR_DNS_LOOKUP_FAILED` (transient resolver error) · `410 ERR_DOMAIN_CLAIM_EXPIRED` ·
  `409 ERR_DOMAIN_ALREADY_VERIFIED` · `404 ERR_DOMAIN_CLAIM_NOT_FOUND` · `429` rate-limited (retry-after).

Context (working folder): `apps/dashboard/components/settings/` — this task edits ONE component + adds a small
poll hook + tests. No backend, no new endpoint, no DB. Milestone `domain-onboarding-softening` task 1 of 3.
Honors (patterns / conventions): Airier tokens (azure `--primary`, `--font-mono` Geist Mono); `data-slot`
markers for test anchors; tanstack-query mutation/`onSuccess`-only-flips-seal; fire-and-forget clipboard;
`role="alert" aria-live` for messages; WCAG AA floor (icon+label never color-alone).
Seams consulted: bff-client (`bffGet`/`bffPost`); tanstack `useQuery.refetchInterval` (function form → poll
only while a pending claim exists) OR a bespoke interval hook honoring `document.visibilitychange`.
Anchors the contract cites: `DomainClaimsSettings`, `CLAIMS_KEY`/`CLAIMS_PATH`, `verifyClaim`, `sealState`,
`bffCode`, `data-slot="dns-challenge"`, the verify error codes above.
Issues/Risks (→ feed §1):
- **R-a (the trap this task removes):** a `400`/`503` on verify today is a LOUD red alert — but during normal
  DNS propagation `400 ERR_DOMAIN_VERIFICATION_FAILED` is the EXPECTED "not live yet" state. Auto-poll must
  reframe 400/503 as a CALM "still checking" state and reserve the loud alert for TERMINAL errors
  (410/409/404). This MUST NOT touch frozen R2 (seal flips only on a 200 success — the calm state keeps the
  seal at pending, so R2 holds).
- **R-b (rate-limit / failure-design):** auto-poll calls a 30-rpm endpoint. ~30s cadence = ~2 calls/min (safe),
  but MUST back off on `429` (respect retry-after) and cap total polling (~15 min ceiling) + pause on tab-hidden
  → never a runaway loop. (CLAUDE.md: design for failure — timeouts/backoff/ceiling.)
- **R-c (scope boundary — feeds the §1 flag):** "email me when it's live" needs a BACKEND scheduled DNS
  re-check + transactional email — OUT of a client-only presentation task. "Verify later" (dismiss the card,
  non-blocking, keep auto-polling) IS client-only and in scope. Registrar deep-link by *nameserver inference*
  also needs backend (NS lookup); a STATIC common-registrar quick-link list is client-only and in scope.
Related intent: milestone D4 (auto-poll ~30s, ceiling ~15 min/tab-blur, manual "Check now") + Scope-In
"soften the STRONG (DNS) rung"; [[domain-onboarding-progressive-trust]]. WHY: DNS-TXT propagation whiplash
("clicked verify, it failed") is the #1 first-run friction the UX study found; auto-poll dissolves it.
Ground SHA: 9ec92b4 (cite symbols, not bare line numbers; any line ref is "as of" this commit)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Soften the DNS-TXT verify flow on the domain-claims console — the challenge verifies ITSELF (auto-poll
+ auto-flip), a not-yet-propagated record reads as a calm "still checking" (not a red alarm), and the admin
gets a one-block copy, a "not the DNS owner?" hand-off, static registrar quick-links, and a "verify later"
that never blocks the session. Presentation + a bounded client poll — verification semantics UNCHANGED.
Framings weighed:
- **Client-driven auto-verify poll (CHOSEN)** — poll `POST /verify` on a bounded interval per pending claim;
  the seal flips from the same success path R2 already owns. No backend, no new endpoint; the endpoint that
  re-checks DNS is verify, and only verify.
- Server push / websocket "verified" event (rejected) — needs backend + a new channel; over-built for a
  propagation wait; out of a presentation task.
- Poll the LIST GET and hope status flips (rejected — WRONG) — the GET never re-checks DNS; only `POST /verify`
  transitions pending→verified. Grounded fact (R-a), not a preference.

Must:
<must>
  - M1 Auto-poll: while ≥1 claim is `status="pending"` AND `sealState()="pending"` (not expired) AND the tab
    is visible, the console calls `POST /verify` for that claim on a ~30s cadence, with NO click.
  - M2 Auto-flip: a `200` verify response flips that claim's seal to verified (reusing the frozen R2 success
    path — `setQueryData`), stops that claim's poll, and announces it via `aria-live`.
  - M3 Calm-waiting reframe: a `400 ERR_DOMAIN_VERIFICATION_FAILED` or `503 ERR_DNS_LOOKUP_FAILED` during
    AUTO-poll shows a calm, non-destructive "Waiting for DNS to propagate — checking automatically…" status on
    the claim/challenge (NOT the red `role="alert"` destructive message), and KEEPS polling.
  - M4 Terminal-error alert: a `410`/`409`/`404` verify response STOPS that claim's poll and surfaces the
    loud, actionable message (expired → reissue; already-verified-elsewhere; not-found) — the alarm is reserved
    for states the admin must act on.
  - M5 Bounded/fail-safe poll: back off on `429` (pause ≥ retry window, then resume); stop auto-polling a claim
    after a ~15-min ceiling; pause on `document.hidden` and resume on visible (ceiling clock persists) — never
    a runaway loop. A manual "Check now" button forces an immediate poll tick at any time.
  - M6 One-block copy: the `dns-challenge` card offers a single "Copy record" action that copies type+name+value
    as one block, ADDITIVE to the existing per-field copies.
  - M7 Not-the-DNS-owner hand-off: a "Not the DNS owner?" affordance packages the domain + exact record +
    short instructions into a shareable message (mailto: compose, clipboard fallback) — client-only.
  - M8 Registrar deep-link display: render the registrar deep-link from the `registrar-hint` backend
    (CONSUMED — `{ registrar, deep_link_url, fallback }` per that task's §3); when `fallback` is true or the
    lookup is unavailable, degrade to a static "Open your DNS provider" common-registrar list (new tab,
    `rel="noopener noreferrer"`). Display only — no inference in the dashboard.
  - M9 Verify later: a "Verify later" affordance dismisses the challenge card WITHOUT blocking; the claim stays
    pending in the table and continues auto-polling; copy points to the "notify me" option (M10) for walking away.
  - M10 Notify-me-when-live opt-in UI: a "Email me when it's live" control on a pending claim that calls the
    CONSUMED backend `POST /admin/domain-claims/{id}/notify` (opt-in) / `DELETE …/notify` (opt-out) — body
    carries NO email (server-derived). Reflect the opted-in state from the list's `notify_requested_at`; when
    the background watch verifies and `notified_at` is set the row shows verified as usual. Purely the UI for the
    `domain-verify-notify` backend — the dashboard neither schedules nor emails.
</must>
Reject:
<reject>
  - Ra A `400`/`503` during auto-poll -> MUST NOT render the destructive `role="alert"` message or flip the
    seal -> stays a calm "still checking" state, seal stays pending (frozen R2 preserved).
  - Rb A `429` during auto-poll -> MUST NOT keep hammering -> back off ≥ the retry window before the next tick.
  - Rc A verified (`sealState()="verified"`) or expired claim -> MUST NOT be auto-polled -> poll set excludes it.
  - Rd Tab hidden -> MUST NOT keep polling -> poll pauses until visible.
</reject>
After:
<after>
  - A pending claim whose TXT record is live flips to verified on its own within ~one poll interval, no click.
  - An admin who published nothing sees a calm "still checking", never a red failure — the session is never blocked.
  - The exact record is copyable as one block AND hand-off-able to whoever owns DNS; a "verify later" leaves a
    self-updating pending row.
  - Verification semantics, the frozen verify endpoint, the seal-flips-only-on-200 rule (R2), and AUTO-JOIN are
    all UNCHANGED — grep shows no edit under `apps/gateway/` or to `resolve_verified_tenant*`.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The two CONSUMED backend shapes (`domain-verify-notify` notify opt-in + `registrar-hint` deep-link) freeze
    at their tasks' contracts in the SAME approval as this one — lowest confidence only in ordering, not shape:
    this dashboard task depends on both, so their §3 shapes must be frozen before/with this one (component-pillar:
    FE holds until BE freezes). If a backend shape shifts after freeze: cost = a change-request that ripples here.
    Mitigated by freezing all three together (backend-first) in one gate.
  - [x] Tin chose backend-backed notify + NS-inferred registrar (2026-07-19) and to SPLIT into 3 tasks — so this
    task is DASHBOARD-only and CONSUMES the two backend contracts (no scheduler/email/NS in this scope).
  - [x] The seal-flip path is reusable for auto-flip — confirmed: `verifyClaim.onSuccess` already does the
    in-place `setQueryData`; auto-poll reuses the same mutation, so R2 holds by construction.
  - [x] ~30s cadence is safe against `domain_claim_verify_rpm=30` — confirmed: ~2 calls/min ≪ 30/min; 429
    backoff (M5) covers the multi-claim/multi-tab edge.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Pending record goes live → auto-flips with no click   # M1,M2
  Given a claim for acme.com is pending and its seal is pending
  And the tab is visible
  When ~one poll interval elapses and POST /verify now returns 200 verified
  Then the console flips acme.com's seal to verified without any click
  And an aria-live region announces the domain is verified
  And that claim is removed from the auto-poll set

Scenario: Record not yet propagated reads as calm "still checking", not an alarm   # M3, Ra
  Given a pending claim being auto-polled
  When POST /verify returns 400 ERR_DOMAIN_VERIFICATION_FAILED (record not live yet)
  Then a calm "Waiting for DNS to propagate — checking automatically…" status shows on the claim
  And NO destructive role="alert" message is rendered
  And the seal stays pending (frozen R2 unchanged)
  And auto-poll continues

Scenario: Transient resolver error keeps the calm state   # M3, Ra
  Given a pending claim being auto-polled
  When POST /verify returns 503 ERR_DNS_LOOKUP_FAILED
  Then the calm "still checking" status remains (no red alert)
  And auto-poll continues

Scenario: Expired challenge stops the poll with an actionable alert   # M4
  Given a pending claim being auto-polled
  When POST /verify returns 410 ERR_DOMAIN_CLAIM_EXPIRED
  Then a loud actionable "challenge expired — request a new one" message shows
  And auto-poll stops for that claim

Scenario: Rate-limit backs off instead of hammering   # M5, Rb
  Given a pending claim being auto-polled
  When POST /verify returns 429 rate-limited
  Then the next verify tick is deferred at least until the retry window passes
  And auto-poll does not fire again before then

Scenario: Auto-poll is bounded and tab-aware   # M5, Rc, Rd
  Given a pending claim being auto-polled
  When the tab becomes hidden
  Then auto-poll pauses until the tab is visible again
  And a verified or expired claim is never added to the auto-poll set
  And after the ~15-min ceiling auto-poll stops, leaving the manual "Check now" button

Scenario: Manual "Check now" forces an immediate tick   # M5
  Given a pending claim
  When the admin clicks "Check now"
  Then POST /verify is called immediately (not waiting for the next interval)
  And a 200 flips the seal; a 400/503 shows the same calm "still checking" state

Scenario: One-block copy copies the whole record   # M6
  Given the dns-challenge card is shown for a fresh claim
  When the admin clicks "Copy record"
  Then type + name + value are written to the clipboard as one block
  And the existing per-field "Copy" buttons still work (additive)

Scenario: Not-the-DNS-owner hand-off packages the record   # M7
  Given the dns-challenge card is shown
  When the admin uses "Not the DNS owner?"
  Then a shareable message containing the domain, the exact record, and short instructions is composed
    (mailto: with clipboard fallback)
  And nothing about the claim's verification state changes

Scenario: Registrar deep-link is shown, degrading to a static list   # M8
  Given the dns-challenge card is shown and registrar-hint returns { registrar, deep_link_url, fallback:false }
  Then a deep-link to that registrar's DNS panel is shown (new tab, rel="noopener noreferrer")
  And when the hint returns fallback:true, a static "Open your DNS provider" common-registrar list is shown instead

Scenario: Verify later leaves a self-updating pending row   # M9
  Given the dns-challenge card is shown
  When the admin chooses "Verify later"
  Then the challenge card is dismissed without blocking
  And the claim remains pending in the table and continues auto-polling

Scenario: Notify-me opt-in calls the backend without an email field   # M10
  Given a pending claim
  When the admin toggles "Email me when it's live"
  Then POST /admin/domain-claims/{id}/notify is called with an empty body (no email)
  And the row reflects the opted-in state from notify_requested_at
  And toggling it off calls DELETE …/notify
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This is a DASHBOARD (presentation + client-poll) task — the "shape" frozen here is OBSERVABLE console
behavior + its test anchors, NOT a new HTTP endpoint. It consumes the FROZEN verify endpoint unchanged.

```
COMPONENT  DomainClaimsSettings  (apps/dashboard/components/settings/DomainClaimsSettings.tsx)

AUTO-POLL (client only; no new endpoint) — calls the FROZEN:
  POST /admin/domain-claims/{id}/verify   body: {}
    200 -> { claim_id, status:"verified", verified_at }   → flip seal (R2 path), stop poll, aria-live announce
    400 ERR_DOMAIN_VERIFICATION_FAILED  → calm "still checking", KEEP polling, seal stays pending
    503 ERR_DNS_LOOKUP_FAILED           → calm "still checking", KEEP polling
    410 ERR_DOMAIN_CLAIM_EXPIRED        → loud actionable message, STOP poll for claim
    409 ERR_DOMAIN_ALREADY_VERIFIED     → loud actionable message, STOP poll for claim
    404 ERR_DOMAIN_CLAIM_NOT_FOUND      → loud message, STOP poll for claim
    429 (rate-limited)                  → back off ≥ retry window, then resume
  poll set   = claims where status="pending" AND sealState()="pending"   (verified/expired excluded)
  cadence    = POLL_INTERVAL_MS ≈ 30_000
  ceiling    = POLL_CEILING_MS ≈ 900_000 per claim; pause while document.hidden (ceiling clock persists)
  manual     = "Check now" button forces an immediate tick (replaces/renames "Verify now")

CONSOLE ANCHORS (data-slot / roles the red suite asserts on):
  data-slot="dns-challenge"        (existing) — the challenge card, now also hosting:
    - a "Copy record" control (M6) copying "Type: …\nName: …\nValue: …" as one block
    - data-slot="dns-handoff"      (M7) — "Not the DNS owner?" → mailto: compose (clipboard fallback)
    - data-slot="registrar-links"  (M8) — the registrar deep-link from registrar-hint; degrades to a static
                                   "Open your DNS provider" list on fallback (new tab, rel="noopener noreferrer")
    - a "Verify later" control     (M9) — dismiss card, claim stays pending + auto-polling
    - data-slot="notify-optin"     (M10) — "Email me when it's live" toggle → POST/DELETE …/notify
  per-pending-row calm status      (M3) — a non-destructive "Waiting for DNS to propagate — checking
                                   automatically…" indicator; NOT role="alert"/text-destructive
  role="alert" destructive message — RESERVED for terminal (410/409/404) + create/revoke errors (M4)

CONSUMED — frozen at sibling tasks in this same milestone (dashboard only DISPLAYS/CALLS; no BE logic here):
  domain-verify-notify (SECURITY):  POST /admin/domain-claims/{id}/notify  body {} → 200 claim(+notify_requested_at,notified_at)
                                     DELETE …/notify → 204 ; list items gain notify_requested_at|notified_at
  registrar-hint:                    GET /admin/domain-claims/registrar-hint?domain= → { domain, registrar: string|null, deep_link_url: string|null, fallback: bool }
  (both freeze WITH this task — backend-first — so the names above resolve at build.)

UNCHANGED (frozen, inherited — a test guards each):
  - seal flips ONLY on a 200 verify success (R2)                       — apps/dashboard DomainStatusSeal/sealState
  - the verify endpoint, its error codes, rate limit, DNS semantics    — apps/gateway (NOT edited)
  - AUTO-JOIN routing (resolve_verified_tenant*)                       — apps/gateway (NOT edited)
```

Glossary deltas: none new (this task uses existing terms; the milestone's member-verified/owner-verified/
invite-by-domain terms belong to tasks 2 & 3). "Auto-poll" is an implementation detail, not a domain term.
Status: FROZEN @ v1 — approved by Tin Dang, 2026-07-20 (after UDD wireframe design-confirm: artifact 4373b8af-1ab5-47c8-995a-847471246e9e · capture .add/design/captures/dns-verify-softeners.html). Consumes the frozen domain-verify-notify + registrar-hint contracts.
Reported: yes — freeze report rendered 2026-07-19; UDD wireframe confirmed by Tin 2026-07-20 before freeze
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (of the new/changed lines in DomainClaimsSettings + the poll hook)
Plan (one test per scenario, asserting behavior not internals — vitest + @testing-library/react, fake timers +
mocked bff-client; assert on rendered DOM / data-slots, never internals):
<test_plan>
  - test_auto_flip_on_200: pending claim + mocked verify→200 / advance one interval / seal shows verified + aria-live announced · covers M1,M2
  - test_calm_state_on_400: verify→400 VERIFICATION_FAILED / advance / calm "checking automatically" shown, NO role="alert" destructive, seal still pending · covers M3,Ra
  - test_calm_state_on_503: verify→503 LOOKUP_FAILED / advance / calm state stays, still polling · covers M3
  - test_terminal_alert_on_410: verify→410 EXPIRED / advance / loud actionable message + poll stops · covers M4
  - test_backoff_on_429: verify→429 / advance one interval / no second verify call before the retry window · covers M5,Rb
  - test_pause_on_tab_hidden: dispatch visibilitychange hidden / advance / no verify call fires · covers M5,Rd
  - test_verified_never_polled: a verified claim / advance intervals / verify never called for it · covers Rc
  - test_check_now_immediate: click "Check now" / verify called at once (no interval wait) · covers M5
  - test_copy_record_one_block: click "Copy record" / clipboard.writeText got type+name+value block; per-field copies still present · covers M6
  - test_handoff_composes_message: click "Not the DNS owner?" / mailto/clipboard payload contains domain+record+instructions; claim state unchanged · covers M7
  - test_registrar_deeplink_and_fallback: hint fallback:false → deep-link anchor (target=_blank rel="noopener noreferrer"); fallback:true → static provider list · covers M8
  - test_verify_later_dismisses_nonblocking: click "Verify later" / challenge card gone, claim row still pending + still polled · covers M9
  - test_notify_optin_calls_backend_no_email: toggle "Email me when it's live" / POST …/notify empty body, opted-in state reflected; toggle off → DELETE …/notify · covers M10
</test_plan>

Tests live in: `apps/dashboard/tests/dns-verify-softeners.test.tsx` — a NEW sibling suite under the dashboard's
central `tests/` dir (the repo convention; the prior console suite `apps/dashboard/tests/domain-claims-console.test.tsx`
is FROZEN and MUST NOT be edited). MUST run red (missing implementation) before Build. Fake timers + mocked bff-client;
mirror the existing suite's QueryClientProvider `Wrapper` + bff mock setup.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
`apps/dashboard/components/settings/DomainClaimsSettings.tsx`
`apps/dashboard/components/settings/`   (a new sibling — e.g. `useVerifyPoll.ts` poll hook + small subcomponents; DIRECTORY token covers the subtree)
`apps/dashboard/tests/dns-verify-softeners.test.tsx`   (the NEW red suite only)
Strategy (ordered batches): 1. Extract a bounded `useVerifyPoll` hook (per-pending-claim interval; visibility-pause;
15-min ceiling; 429 backoff; reuses the `verifyClaim` mutation so the R2 success path stays the single seal-flip
site). 2. Reframe verify error handling: split terminal (410/409/404 → loud alert) from not-yet-propagated
(400/503 → calm per-row "still checking"); wire auto-poll to the calm path, keep manual "Check now" + create/revoke
on the alert path. 3. Add the challenge-card affordances: one-block "Copy record", `dns-handoff` mailto, static
`registrar-links`, "Verify later" (dismiss + keep polling). 4. aria-live announce on auto-flip. Airier tokens, AA.
Persona (required): generic (no project persona file fits a dashboard-UX task yet; SOUL.md voice governs).
Spawn isolation (default): worktree for any build/verify subagent spawn.
Known-problem fixes: runaway poll → hard ceiling + visibility pause + 429 backoff (M5); React StrictMode
double-mount / leaked intervals → cleanup in the hook's effect return; timer flakiness in tests → vitest fake
timers; clipboard crash in jsdom → reuse fire-and-forget `copyToClipboard`; a 400 must NEVER reach the destructive
alert path during auto-poll (that would re-introduce the very trap — R-a) → route auto-poll failures through the
calm path only.
Strategy actually used: <fill at VERIFY>
Safety rule (feature-specific): auto-poll NEVER writes the seal directly — the seal transitions ONLY through the
existing verify `onSuccess` (frozen R2); a non-200 auto-poll response changes messaging only, never claim state.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token with "/" = project root · a bare name = sibling of the previous token's dir · a DIRECTORY token covers its whole subtree (diverges from §4's non-recursive counting) · outside-root resolutions drop fail-closed · absent line = UNDECLARED (grandfathered, never retro-red) · enforcement live: a completing verify gate refuses an out-of-scope build (scope_violation → self-heal); check surfaces it. EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: <agent-id | self>
1. Security: <CLEAR | HARD-STOP: finding>
2. Concurrency: <CLEAR | RESIDUE: finding>
3. Architecture: <CLEAR | RESIDUE: finding>
Verdict: <PASS | HARD-STOP>
Residue: <none | summary>
Binding: <yes — mechanical | advisory — <sensitivity>>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- Security is ALWAYS HARD-STOP; record exactly one outcome — no silent pass. The Advisor 3-lens and Refute-read verdicts are audit-measured (`advisor_verdict_unrecorded` · `refute_unrecorded`), never engine-blocked; a human spot-audit backstops anything unrecorded. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
