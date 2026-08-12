# TASK: Soften the DNS-TXT verify flow: auto-poll + auto-flip, verify-later/notify, not-the-DNS-owner hand-off, registrar deep-link, one-block copy

slug: dns-verify-softeners · created: 2026-07-19 · stage: production
milestone: domain-onboarding-softening
component: dashboard
sensitivity: architecture
autonomy: auto
phase: done

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
    a runaway loop. The EXISTING manual "Verify now" button (unchanged — kept exactly as the frozen
    `domain-claims-console` suite asserts it, incl. its LOUD `role="alert"` on a manual 400/503) still forces
    an immediate verify at any time. (v2 change-request, Tin 2026-07-20: softening is the ADDITIVE auto-poll
    layer only; the manual button is NOT renamed and its manual-failure alert is NOT reframed — reframing the
    manual path would break the frozen sibling suite. See §3 Status v2.)
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

Scenario: The existing manual "Verify now" button still forces an immediate verify   # M5 (v2)
  Given a pending claim being auto-polled on a long interval
  When the admin clicks "Verify now"
  Then POST /verify is called immediately (not waiting for the next auto-poll interval)
  And a 200 flips the seal
  # The manual path's LOUD alert on a 400/503 is UNCHANGED and owned by the frozen
  # domain-claims-console suite (test_verify_mismatch_alert_no_flip / _dns_lookup_distinct_alert);
  # this task does NOT reframe it. Softening is the auto-poll path only.

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
  manual     = the EXISTING "Verify now" button (KEPT — label + its manual-path behavior UNCHANGED) forces an
               immediate verify; the calm-reframe below is AUTO-POLL-ONLY, it does NOT touch the manual path
  NOTE (v2)  = the 400/503→calm reframe applies to the AUTO-POLL responses ONLY. A MANUAL "Verify now" click on
               400/503 keeps its existing LOUD role="alert" (owned by the frozen domain-claims-console suite,
               NOT edited here). This narrows v1 which over-reached by renaming the button + reframing the manual path.

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
  - the MANUAL "Verify now" button: its label AND its LOUD role="alert" on a manual 400/503 (v2)
                                                                        — apps/dashboard domain-claims-console.test.tsx (NOT edited)
  - the verify endpoint, its error codes, rate limit, DNS semantics    — apps/gateway (NOT edited)
  - AUTO-JOIN routing (resolve_verified_tenant*)                       — apps/gateway (NOT edited)
```

Glossary deltas: none new (this task uses existing terms; the milestone's member-verified/owner-verified/
invite-by-domain terms belong to tasks 2 & 3). "Auto-poll" is an implementation detail, not a domain term.
Least-sure flag surfaced at freeze: [contract] The v1→v2 narrowing: whether softening ONLY the auto-poll path
(while the manual "Verify now" button keeps its loud alert) still delivers the milestone's "no more clicked-
verify-and-it-failed" goal. Judgement (Tin-confirmed 2026-07-20): YES — with auto-poll auto-flipping calmly in
the background, the admin rarely clicks manually at all; the loud manual alert becomes a rarely-hit power path,
not the first-run friction the UX study flagged. The alternative (reframe the manual path too) would break the
frozen domain-claims-console suite (test_verify_now_success / _mismatch_alert / _dns_lookup_distinct) — forbidden.
Cost if wrong: a follow-on delta to also soften the manual alert, which would require re-opening the sibling
contract; deferred, not lost.
Status: FROZEN @ v2 — approved by Tin Dang, 2026-07-20 (v2 change-request: narrows v1 — the softening is the
ADDITIVE auto-poll layer only; the MANUAL "Verify now" button label + its loud alert are kept UNCHANGED because
the frozen domain-claims-console suite asserts them and MUST NOT be weakened. v1 approved same day after UDD
wireframe design-confirm: artifact 4373b8af-1ab5-47c8-995a-847471246e9e · capture .add/design/captures/dns-verify-softeners.html).
Consumes the frozen domain-verify-notify + registrar-hint contracts.
Reported: yes — freeze report rendered 2026-07-19; UDD wireframe confirmed by Tin 2026-07-20 before freeze

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
  - test_manual_verify_now_still_immediate: with auto-poll configured on a long interval, click the existing "Verify now" / verify called at once (no interval wait), a 200 flips the seal — the manual button coexists with auto-poll, unchanged · covers M5 (v2)
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

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
`apps/dashboard/components/settings/DomainClaimsSettings.tsx`
`apps/dashboard/components/settings/`   (a new sibling — e.g. `useVerifyPoll.ts` poll hook + small subcomponents; DIRECTORY token covers the subtree)
`apps/dashboard/tests/dns-verify-softeners.test.tsx`   (the NEW red suite only)
`apps/dashboard/tests/mocks/handlers.ts`   (ADDITIVE-only: one INITIAL default MSW handler for the NEW GET /admin/domain-claims/registrar-hint read — the established handlers.ts precedent so resetHandlers() preserves it and the frozen create-flow test, which renders the card without mocking the new read, stays green under onUnhandledRequest:"error")
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
Strategy actually used: As planned, with the v2 change-request narrowing discovered at build-entry. (1) Extracted `useVerifyPoll.ts` — a single cleared interval, per-claim in-flight/ceiling/backoff/visibility guards, seal-flip handed back via `onVerified` so `applyVerified` stays the ONE frozen-R2 seal-flip site. (2) Split auto-poll error handling (`runVerify`): 400/503→calm, 410/409/404→terminal, 429→60s backoff — WITHOUT touching the manual `verifyClaim` mutation (its loud alert kept verbatim per the frozen sibling suite). (3) Card affordances (Copy record / dns-handoff mailto / registrar-links / Verify later / notify-optin) + a `registrar-hint` display query gated on `challenge`. (4) `role="status"` aria-live on auto-flip. Added a defence-in-depth `isSafeHttpUrl` scheme-guard on the deep-link href and one ADDITIVE default MSW handler (registrar-hint fallback) so the frozen create-flow test stays green under `onUnhandledRequest:"error"` (scope expanded + re-snapshotted via `phase build`). Test seam: an optional `poll` prop injects fast timings so the suite uses REAL timers with MSW (no fake-timer precedent in tests/).
Safety rule (feature-specific): auto-poll NEVER writes the seal directly — the seal transitions ONLY through the
existing verify `onSuccess` (frozen R2); a non-200 auto-poll response changes messaging only, never claim state.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [x] no test or contract was altered during build (the FROZEN sibling `domain-claims-console.test.tsx` is untouched + green 8/8; the only test-dir edits are the NEW suite + one ADDITIVE default handler, both in declared §5 scope)
- [x] the green was EARNED, not gamed — self refute-read + an independent add-verify subagent adversarial pass (see Refute-read verdict below)
- [x] concurrency / timing of the risky operation is safe (single cleared interval; per-claim in-flight guard + ceiling + visibility-pause + 429 backoff; seal writes ONLY via applyVerified — see Advisor lens 2)
- [x] no exposed secrets, injection openings, or unexpected dependencies (mailto/registrar built from server-derived challenge values; `isSafeHttpUrl` scheme-guards the deep-link href; every external anchor rel="noopener noreferrer"; notify body empty)
- [x] layering & dependencies follow CONVENTIONS.md (presentation-only; bff-client seam; no new package; hook extracted beside the component)
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] GREEN BAR: `vitest (ci.yml dashboard job, working-directory: apps/dashboard)` — new `dns-verify-softeners.test.tsx` 14/14; frozen `domain-claims-console.test.tsx` 8/8; FULL dashboard suite 1560/1560 across 174 files; `tsc --noEmit` exit 0.
- [x] A pending claim whose TXT record goes live flips its seal to "Verified" with NO click within ~one auto-poll interval + `role="status"` aria-live announces it — test_auto_flip_on_200 (no userEvent click before the flip; asserts the status region + poll-set removal).
- [x] A 400/503 during AUTO-poll shows a calm non-`role="alert"` "Waiting for DNS to propagate — checking automatically…" indicator, keeps polling, seal stays "Pending DNS" — test_calm_state_on_400 / _503 (assert NO destructive alert; seal unchanged).
- [x] The MANUAL "Verify now" button + its LOUD `role="alert"` on a manual 400/503 are UNCHANGED (v2) — the frozen `domain-claims-console` verify trio stays green (8/8).
- [x] Auto-poll is bounded: 429→60s backoff (no 2nd call in 300ms), 70ms ceiling stops it, tab-hidden pauses it (0 calls), verified/expired never polled — test_backoff_on_429 / _ceiling / _pause_on_tab_hidden / _verified_never_polled.
- [x] The challenge card gains one-block "Copy record", `dns-handoff` mailto, `registrar-links` (deep-link when fallback:false / static list on fallback:true, new tab rel="noopener noreferrer"), "Verify later" (dismiss + keep polling), `notify-optin` (POST/DELETE …/notify, empty body) — the M6–M10 tests + data-slots.
- [x] `grep` shows NO edit under `apps/gateway/` and NO edit to `resolve_verified_tenant*` — `git diff --name-only` = apps/dashboard + .add only.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `useVerifyPoll` imported+called by DomainClaimsSettings; `RegistrarLinks`/`DnsHandoff`/`recordBlock`/`isSafeHttpUrl`/`applyVerified`/`runVerify`/`notifyOptin`/`notifyOptout` all referenced in the render; `pollStatus` drives the per-row calm/terminal indicators; the new default registrar-hint handler is in `defaultHandlers`.
- [x] DEAD-CODE (code) — no orphaned symbol; `VERIFY_POLL_BACKOFF_MS`/`DEFAULT_VERIFY_POLL`/`PollRowStatus`/`VerifyOutcome` all consumed; tsc `noUnusedLocals` clean (exit 0).
- [x] SEMANTIC (prose) — read the frozen sibling suite in full: it still asserts the manual "Verify now" label (test_verify_now_success_flips_seal:193) + the loud role="alert" on manual 400/503 (test_verify_mismatch_alert_no_flip / _dns_lookup_distinct_alert) — the v2 narrowing is honest, not a weakening.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 cites resolves: `DomainClaimsSettings`, `CLAIMS_KEY`/`CLAIMS_PATH`, `verifyClaim` (manual, unchanged), `sealState`, `bffCode`, `data-slot="dns-challenge"`, the verify error codes — all present; new anchors `dns-handoff`/`registrar-links`/`notify-optin`/`role="status"` render. Backend consumed shapes confirmed against `schemas.py` (RegistrarHintResponse:105, DomainClaimListItem notify fields:46) + `error_catalog.py` (400/503/410/409/404, NOT 422).
- [x] no anchor moved/renamed since Ground SHA 9ec92b4 — DomainStatusSeal/sealState + the manual verify path are byte-stable.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: self + agent a7faac2315908ab97 (add-verify, independent) · adversarially checked: no-click on auto-flip (no userEvent before the seal turns); calm-path asserts NO role="alert" + seal-stays-pending (a broken 400 path would surface a loud alert → red); 429 backoff proven by no-2nd-call-in-300ms; ceiling proven by stop-after-otherwise-live-400-poll; verified_never_polled guards the pollable-set filter; notify body asserted email-free; mailto asserted to contain domain+record; seal single-flip-site (applyVerified) is the ONLY setQueryData caller; frozen sibling 8/8 re-run green.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: a7faac2315908ab97 (add-verify, independent) + self
1. Security: CLEAR — mailto + registrar links built from server-derived challenge values (no user free-text); `isSafeHttpUrl` scheme-guards the deep-link href (js:/data: fails closed to the static list); every external anchor target="_blank" rel="noopener noreferrer"; notify opt-in body empty (server-derived recipient).
2. Concurrency: CLEAR — one interval, cleared on unmount; per-claim in-flight guard prevents overlapping verifies; ceiling + visibility-pause + 60s 429 backoff bound the loop; the seal is written ONLY via `applyVerified` (frozen-R2 single-flip site) — the poll never writes it directly; no double-flip.
3. Architecture: CLEAR — presentation-only additive layer; the v2 narrowing is honest (frozen manual "Verify now" + its loud alert preserved verbatim, frozen sibling 8/8); no `apps/gateway/` or `resolve_verified_tenant*` edit; auto-join semantics untouched.
Verdict: PASS
Residue: none
Binding: advisory — architecture (auto-gate on clean evidence; no security HARD-STOP, no concurrency/architecture residue → auto-PASS)

### GATE RECORD
Reported: yes — evidence rendered (new 14/14 · frozen sibling 8/8 · full dashboard 1560/1560 · tsc 0 · scope=dashboard-only) before this outcome.
Outcome: PASS
component: dashboard · expected green-bar: vitest (ci.yml dashboard job, working-directory: apps/dashboard) · verify: cd apps/dashboard && npx vitest run
Reviewed by: auto-gate (autonomy:auto, architecture — clean 3-lens + EARNED refute-read, no residue, no security finding) · self + add-verify agent a7faac2315908ab97 · date: 2026-07-20

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v2 (approved by Tin Dang, 2026-07-20 (v2 change-request: narrows v1 — the softening is the)
- [AI] build — strategy used: As planned, with the v2 change-request narrowing discovered at build-entry. (1) Extracted `useVerifyPoll.ts` — a single cleared interval, per-claim in-flight/ceiling/backoff/visibility guards, seal-flip handed back via `onVerified` so `applyVerified` stays the ONE frozen-R2 seal-flip site. (2) Split auto-poll error handling (`runVerify`): 400/503→calm, 410/409/404→terminal, 429→60s backoff — WITHOUT touching the manual `verifyClaim` mutation (its loud alert kept verbatim per the frozen sibling suite). (3) Card affordances (Copy record / dns-handoff mailto / registrar-links / Verify later / notify-optin) + a `registrar-hint` display query gated on `challenge`. (4) `role="status"` aria-live on auto-flip. Added a defence-in-depth `isSafeHttpUrl` scheme-guard on the deep-link href and one ADDITIVE default MSW handler (registrar-hint fallback) so the frozen create-flow test stays green under `onUnhandledRequest:"error"` (scope expanded + re-snapshotted via `phase build`). Test seam: an optional `poll` prop injects fast timings so the suite uses REAL timers with MSW (no fake-timer precedent in tests/).
- [AI] verify — gate PASS (reviewed by auto-gate (autonomy:auto, architecture — clean 3-lens + EARNED refute-read, no residue, no security finding))

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

