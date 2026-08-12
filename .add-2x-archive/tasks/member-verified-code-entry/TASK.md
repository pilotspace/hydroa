# TASK: Dashboard: member-verified 6-digit code-entry screen + rung-aware climb seal (D1)

slug: member-verified-code-entry · created: 2026-07-20 · stage: production
milestone: domain-onboarding-softening
component: dashboard
sensitivity: architecture
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `apps/dashboard/components/settings/DomainClaimsSettings.tsx:DomainClaimsSettings` — the Domains-tab console (OWNER-scoped, pure BFF pass-through). The inline member-verify code block is a NEW affordance rendered on a PENDING claim row. REUSE in place: `interface DomainClaimListItem` (add-consumes the new frozen field `member_verified_at`), `ChallengeField`/`recordBlock`/`copyToClipboard` (copy idiom), `getErrorTitle`/`bffCode` (BffError→code/title), the `applyVerified` single-seal-flip site, and the calm-vs-loud message convention (`role="status"` calm vs the KEPT-VERBATIM manual `role="alert"`).
  - `apps/dashboard/components/settings/DomainStatusSeal.tsx:DomainStatusSeal` + `sealState()` — the ONE status marker. Today it derives 3 states from `(status, expires_at)`. This task EXTENDS the derivation to the rung climb: add `member-verified` as a derived state from `(status, member_verified_at)` — azure/person, label "Member-verified"; keep `verified`→"Owner-verified" (lock, sealed) and the warning `pending`. `export type DomainSealState` grows a member arm. Icon+label BOTH carry meaning (WCAG 1.4.1).
  - `apps/dashboard/components/overview/OnboardingChecklist.tsx:OnboardingChecklist` + `interface Step` (`id` union) — append a "Confirm your work email domain" step, role-gated + deep-linking to `/app/settings?tab=domains`; completion derives from the claim list (member_verified_at set OR verified). Mounted at `components/overview/OverviewPage.tsx:203`.
  - `apps/dashboard/components/settings/SettingsPage.tsx` — mounts `<DomainClaimsSettings/>` under the URL-controlled `domains` tab (`?tab=domains`); the checklist deep-link target. No change expected beyond confirming the tab route.
Context (working folder):
  - `apps/dashboard/components/settings/useVerifyPoll.ts` — the shipped bounded auto-poll hook (dns-verify-softeners §3 FROZEN @ v2). CONTEXT only: the code path is user-submitted, NOT auto-polled; reuse its calm-vs-terminal taxonomy shape (`VerifyOutcome`) as the mental model for error handling, not the hook itself.
  - `apps/dashboard/lib/bff-client.ts` (`bffPost`, `BffError`, `ProblemDetail`) + `lib/resilient-fetch.ts` — the ONLY FE→gateway path. No new BFF route file: `app/api/gw/[...path]/route.ts` is a catch-all proxy, so `bffPost("/admin/domain-claims/{id}/member-verify", { code })` and `.../member-verify/resend` reach the frozen gateway endpoints as-is (cookie-auth, no Authorization header client-side).
Honors (patterns / conventions):
  - Presentation + BFF pass-through ONLY — every trust/authorization/rate-limit decision is server-side (member-verified-recognition, 6a75579). The code is the ONLY value the UI sends; it NEVER collects or transmits an email or domain (server derives the recipient from the caller's own signup email).
  - Calm-error convention (dns-verify-softeners): recoverable states are `role="status"`, never a loud `role="alert"`. The manual "Verify now" loud alert stays verbatim (v2 narrowing) — do not touch it.
  - Airier tokens: `--primary #2f6df0`, `--accent-soft-foreground #1c4bb8` (AA on `--accent-soft`), success/warning/destructive `-text` variants for AA small text; Geist / Geist Mono (mono for every digit/segment). SSR-safe localStorage only in useEffect (checklist convention).
Seams consulted: FE→gateway = the `/api/gw/[...path]` catch-all BFF proxy (no per-endpoint route). No `.add/SEAMS.md` entry needed.
Anchors the contract cites: `POST /admin/domain-claims/{id}/member-verify` (body `{code}`), `POST /admin/domain-claims/{id}/member-verify/resend` (body `{}`), the `member_verified_at` claim field, `DomainStatusSeal`/`sealState` (extended derivation), `DomainClaimsSettings` (inline block), `OnboardingChecklist` (new step) — all in `apps/dashboard`.
Issues/Risks (→ feed §1):
  - Seal derivation ORDER matters: `verified` (Owner) must win over `member_verified_at` (Member) — a claim can be both; derive Owner first, then Member, then pending/expired. A wrong order silently downgrades an owner to "member". `sealState` currently ignores `member_verified_at` entirely.
  - The existing `sealState` also derives a client-side `expired` state from `expires_at`; the rung climb must compose with it (an expired-but-member-verified claim is a real combination) — decide precedence at Specify.
  - Frozen error taxonomy is richer than dns-verify (adds `ERR_MEMBER_VERIFY_*` invalid/expired/too-many/mismatch/not-eligible + shared `ERR_RATE_LIMITED`/404/409). The BFF drops `Retry-After` (BffError carries status+body only) — a 429 message must be self-contained, not promise a countdown.
  - `DomainClaimListItem` is the frozen read shape; only `member_verified_at: datetime|null` is new. Adding it must not disturb the frozen domain-claims-console / dns-verify-softeners sibling tests (additive only).
  - Auto-advance / paste-fill / backspace-to-prev OTP behavior + inputmode=numeric is net-new interaction with real edge cases (paste of <6 or >6 chars, non-digits, IME) — a genuine test surface for §2.
Related intent: milestone `domain-onboarding-softening` (rung task 4→5 ladder; D1 climb seal Tin-confirmed 2026-07-20, artifact 46b0d3c6). The WHY: give a teammate a low-friction rung-1 trust ("I belong to acme.com") without the DNS-owner burden, while keeping Owner-verified (auto-join-capable) as the sealed top rung. Consumes the FROZEN member-verified-recognition backend (task 4).
Ground SHA: 6a75579 (member-verified-recognition freeze is HEAD; line refs are "as of" this commit — symbols cited above, not bare lines).
UDD DESIGN CONFIRMED by Tin 2026-07-20 — capture `.add/design/captures/member-verified-code-entry.html` (artifact https://claude.ai/code/artifact/162c7e60-1e08-47eb-97a5-7422611860f7). Locked axes: (1) code-entry INLINE on the Settings Domains card (not a modal/route); (2) FIRST-RUN = a new "Confirm your work email domain" step on the Overview OnboardingChecklist deep-linking to it; (3) SIX OTP-style segments (auto-advance, paste-fills-all, backspace-to-prev, inputmode=numeric); + D1 3-state climb seal (Member azure/person → Owner success/lock sealed → Pending warning, Owner wins) + calm role="status" errors (dns-verify-softeners pattern). Build cosmetic: the reused DNS card renders the REAL `ai-proxy-domain-verification=` / `_ai-proxy-challenge.` strings (the wireframe's placeholder brand names are not the spec).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Member-verified 6-digit code entry (inline) + rung-aware climb seal + onboarding step (dashboard consumer of FROZEN member-verified-recognition, 6a75579)
Framings weighed: inline-on-the-Domains-card (chosen — matches UDD-locked axis 1; the code path sits beside the DNS challenge, neither blocks the other) · modal/route (rejected — a separate route hides the "climb" relationship between Member and Owner) · reuse `useVerifyPoll` for the code (rejected — the code is USER-SUBMITTED, never auto-polled; only its calm-vs-terminal taxonomy shape is borrowed as a mental model)

Must:
<must>
  - M1 · SEAL — new arm: `sealState(claim)` returns `"member-verified"` when `member_verified_at` is set AND `status === "pending"`. Renders an azure/accent Badge, a person/User icon, visible label "Member-verified", + sr-only membership assertion — icon AND label each carry meaning independently (WCAG 1.4.1), never colour alone.
  - M2 · SEAL PRECEDENCE — `verified` (Owner) wins over `member-verified`; `member-verified` wins over both `expired` and `pending`. Evaluation order: `verified` → `member-verified` (member_verified_at != null) → `pending` (expires_at > now) → `expired`. A claim that is both DNS-verified and member-verified shows Owner-verified, never a downgrade.
  - M3 · ADDITIVE LABELS — the three EXISTING seal states keep their frozen visible text VERBATIM: "Verified" (success/Lock), "Pending DNS" (warning), "Expired" (destructive). Only the new "member-verified" arm is added, so the frozen `domain-claims-console` / `dns-verify-softeners` seal assertions stay green.
  - M4 · INLINE BLOCK — an expanded 6-digit code-entry block renders on a claim that is NOT verified AND NOT yet member-verified (seal `pending` or `expired`, `member_verified_at == null`); it sits above the reused DNS challenge card. Once member-verified (or Owner-verified) the block collapses; the DNS climb path stays offered.
  - M5 · OTP INPUT — six single-character segments; `inputmode="numeric"`; only digits accepted (a non-digit keypress is ignored, segment unchanged); typing a digit auto-advances focus to the next segment; `role="group"` `aria-label="6-digit confirmation code"`.
  - M6 · BACKSPACE — backspace on an empty segment moves focus to the previous segment and clears it (backspace-to-prev); backspace on a filled segment clears it in place.
  - M7 · PASTE — a paste on any segment strips non-digits from the clipboard text and fills the six segments left-to-right with the first up to 6 digits (paste of >6 → first 6; paste of <6 valid digits → fills what's available, focus the first empty; paste with zero digits → no-op, segments unchanged).
  - M8 · CONFIRM — "Confirm" is an explicit button, disabled until all six segments are filled; on click it submits `bffPost("/admin/domain-claims/{claim_id}/member-verify", { code })` where `code` is the six assembled digits. No auto-submit on the sixth digit (avoids double-submit).
  - M9 · SUCCESS (200) — the returned `DomainClaimListItem` is patched into the cached claim via the single seal-flip idiom (`applyMemberVerified`, parallel to `applyVerified`), setting `member_verified_at`; the seal flips to "Member-verified"; the code block collapses; `status` stays `"pending"` so the DNS/Owner path remains available.
  - M10 · RESEND — a "Resend code" button (always present in the expanded block) submits `bffPost("/admin/domain-claims/{claim_id}/member-verify/resend", {})`; on 200 it shows a calm `role="status"` confirmation ("a fresh code is on its way") and CLEARS all six segments.
  - M11 · CALM ERRORS — every failure of member-verify or resend renders a calm `role="status"` message (NEVER `role="alert"`); each FROZEN error code maps to a distinct human-readable message (§3 map); on a retryable error the six segments STAY PUT so the user can correct without re-typing.
  - M12 · SELF-CONTAINED RATE MESSAGE — the `ERR_RATE_LIMITED` (429) message promises NO countdown / retry-after window (the BFF drops `Retry-After`; `BffError` carries status+body only), only "wait a moment and try again".
  - M13 · ONBOARDING STEP — append one role-gated (owner) `Step` `id:"confirm_domain"` label "Confirm your work email domain" deep-linking to `/app/settings?tab=domains`; `complete` derives from the claim list: `claims.some(c => c.member_verified_at != null || c.status === "verified")`.
  - M14 · SEND-ONLY-THE-CODE — the FE only ever transmits the 6-digit code (member-verify) or an empty body (resend); it NEVER collects, renders an input for, or transmits an email or a domain (the server derives the recipient + domain from the caller's own signup email).
  - M15 · REUSE UNTOUCHED — consuming the new `member_verified_at` field is additive-only: the reused DNS challenge card, its `useVerifyPoll` auto-poll, per-field copy, and the manual "Verify now" LOUD `role="alert"` all stay verbatim (v2 narrowing) — this task adds, never edits, their behavior.
</must>
Reject:
<reject>
  - wrong / non-matching code (still under the attempt cap) -> "ERR_MEMBER_VERIFY_CODE_INVALID" (400)
  - the in-flight code has expired -> "ERR_MEMBER_VERIFY_CODE_EXPIRED" (410)
  - attempt cap reached, code invalidated -> "ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS" (429)
  - per-tenant rate limit hit -> "ERR_RATE_LIMITED" (429, self-contained message, no countdown)
  - claim already DNS-verified (nothing to member-verify) -> "ERR_DOMAIN_CLAIM_NOT_PENDING" (409)
  - claim.domain != the caller's own email domain -> "ERR_MEMBER_VERIFY_DOMAIN_MISMATCH" (403)
  - caller is not the owner -> "ERR_AUTH_FORBIDDEN" (403)
  - claim unknown / other tenant -> "ERR_DOMAIN_CLAIM_NOT_FOUND" (404)
  - resend on a generic/public email domain -> "ERR_DOMAIN_GENERIC" (422)
  - resend on a personal account -> "ERR_MEMBER_VERIFY_NOT_ELIGIBLE" (403)
  - a non-digit keystroke, or a paste containing zero digits -> rejected CLIENT-SIDE (segment(s) unchanged, no request sent — never reaches the server)
  - any unmapped code / non-BffError failure -> generic calm fallback message (no request state corrupted)
</reject>
After:
<after>
  - member-verify 200: the claim carries `member_verified_at` (set); its seal reads "Member-verified"; the code block is collapsed; `status` is still `"pending"`; the DNS/Owner challenge is still offered; the Overview "Confirm your work email domain" step reads complete.
  - resend 200: a calm "fresh code sent" confirmation is shown; the six segments are cleared; the claim is otherwise unchanged (still pending, `member_verified_at` still null until a code is entered).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ SEAL PRECEDENCE for the expired-but-member-verified combination — I rank `member-verified` ABOVE the client-derived `expired` state. Lowest confidence because it is a genuine, un-ranked-by-backend state combination: `member_verified_at` is durable server truth (never cleared on the member path) while `expires_at` governs ONLY the optional DNS/Owner climb window, so an elapsed DNS window must not visually downgrade a real member. If wrong: a member whose DNS challenge window lapsed would falsely read "Member-verified" when Tin expected "Expired". Cost to flip: one ordering line in `sealState`.
  - [ ] Existing seal labels stay VERBATIM ("Verified"/"Pending DNS"/"Expired") rather than the wireframe's "Owner-verified"/"Pending" wording — additive-only (keep frozen sibling tests green) beats wireframe copy (the wireframe is "layout and states, not final pixels"; §0 already treats its brand strings as non-spec). If wrong (Tin wants the relabel): it is a change request that must ALSO retarget the frozen `domain-claims-console` seal assertions via CR + re-cross — not a silent edit.
  - [ ] Confirm is an explicit button (no auto-submit on the 6th digit) — matches the wireframe's "Confirm" button and avoids a double-submit race. If wrong: add submit-on-complete.
  - [ ] The "Resend code" button is ALWAYS present in the expanded block (not conditionally shown per error); the EXPIRED / TOO_MANY messages merely point at it. Matches the wireframe.
  - [ ] The 200 body for both member-verify and resend is the full frozen `DomainClaimListItem` (member-verify with `member_verified_at` set) — patched into the react-query cache; `applyMemberVerified` parallels the existing `applyVerified` single-flip site.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Member-verified seal arm renders   # M1
  Given a claim with status "pending" and member_verified_at set
  When the claims list renders
  Then its seal shows the visible label "Member-verified" with a person icon and an sr-only membership assertion
  And color is not the only signal (icon + label both present)

Scenario: Owner-verified wins over member-verified   # M2
  Given a claim with status "verified" AND member_verified_at set
  When the seal derives
  Then it shows "Verified" (Owner), never "Member-verified"

Scenario: Member-verified wins over an elapsed DNS window   # M2 (⚠ precedence)
  Given a claim with member_verified_at set, status "pending", and expires_at in the past
  When the seal derives
  Then it shows "Member-verified", never "Expired"

Scenario: Existing seal labels stay verbatim   # M3 (additive)
  Given a pending claim (no member_verified_at, expires_at in the future) and a verified claim
  When the list renders
  Then the seals read exactly "Pending DNS" and "Verified" (frozen sibling assertions unbroken)

Scenario: Code block shows on an actionable pending claim   # M4
  Given a pending claim with member_verified_at == null
  When the Domains card renders
  Then the 6-digit code-entry block is visible above the DNS challenge card
  And once member_verified_at is set the block is collapsed while the DNS card remains

Scenario: Only digits, auto-advance   # M5
  Given the empty 6-segment input focused on segment 1
  When the user types "4"
  Then segment 1 shows "4" and focus moves to segment 2
  And typing a non-digit "x" leaves the focused segment unchanged and does not advance

Scenario: Backspace steps back and clears   # M6
  Given segments 1-3 filled and focus on an empty segment 4
  When the user presses Backspace
  Then focus moves to segment 3 and segment 3 is cleared

Scenario: Paste fills all six   # M7
  Given the empty 6-segment input
  When the user pastes "41 92-07" from the clipboard
  Then all six segments fill with 4,1,9,2,0,7 (non-digits stripped, first six taken)
  And a paste of "abc" (zero digits) leaves the segments unchanged

Scenario: Confirm submits the code   # M8
  Given all six segments filled with "419207"
  When the user clicks "Confirm"
  Then bffPost is called with path ".../member-verify" and body { code: "419207" }
  And Confirm is disabled while any segment is empty

Scenario: Success flips the seal and collapses the block   # M9
  Given a pending claim and a valid code entered
  When the server returns 200 with member_verified_at set
  Then the seal flips to "Member-verified", the code block collapses, and status stays "pending"

Scenario: Resend re-arms and clears segments   # M10
  Given the expanded code block with three segments filled
  When the user clicks "Resend code" and the server returns 200
  Then a calm role="status" "fresh code sent" confirmation appears and all six segments are cleared

Scenario: Errors are calm, never loud, segments preserved   # M11
  Given a filled code that the server rejects with 400 ERR_MEMBER_VERIFY_CODE_INVALID
  When the response returns
  Then a role="status" (not role="alert") message "That code doesn't match…" is shown
  And the six segments remain filled (unchanged)

Scenario: Rate-limited message makes no countdown promise   # M12
  Given a submit that the server rejects with 429 ERR_RATE_LIMITED
  When the response returns
  Then the calm message says to wait a moment and retry, with NO countdown / retry-after value
  And the segments remain unchanged

Scenario: Onboarding step appended and derives completion   # M13
  Given an owner whose tenant has a claim with member_verified_at set
  When the Overview onboarding checklist renders
  Then a "Confirm your work email domain" step is present, deep-links to /app/settings?tab=domains, and reads complete
  And the step is absent for a non-owner role

Scenario: The UI never sends an email or domain   # M14
  Given the code-entry block
  When the user confirms or resends
  Then the request body is only { code } or {} — no email/domain field is present or collectible in the UI

Scenario: Reuse stays untouched   # M15
  Given the reused DNS challenge card with its manual "Verify now" action
  When a manual verify fails
  Then its LOUD role="alert" message still renders verbatim (calm code-entry errors do not replace it)

Scenario: Expired code offers a resend   # R:ERR_MEMBER_VERIFY_CODE_EXPIRED (410)
  Given a filled code the server rejects with 410 ERR_MEMBER_VERIFY_CODE_EXPIRED
  When the response returns
  Then a calm role="status" "this code has expired… request a fresh one" message shows and the Resend button is available
  And the claim remains pending, seal unchanged, segments preserved

Scenario: Too many attempts pauses the code   # R:ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS (429)
  Given a filled code the server rejects with 429 ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS
  When the response returns
  Then a calm "too many tries… send a new code" message shows with Resend available
  And the claim and seal are unchanged

Scenario: Already DNS-verified   # R:ERR_DOMAIN_CLAIM_NOT_PENDING (409)
  Given a confirm the server rejects with 409 ERR_DOMAIN_CLAIM_NOT_PENDING
  When the response returns
  Then a calm "this domain is already verified" message shows
  And the seal and claim are unchanged

Scenario: Mailbox domain mismatch   # R:ERR_MEMBER_VERIFY_DOMAIN_MISMATCH (403)
  Given a confirm the server rejects with 403 ERR_MEMBER_VERIFY_DOMAIN_MISMATCH
  When the response returns
  Then a calm "this code was sent to a different email domain…" message shows
  And the claim and seal are unchanged, segments preserved

Scenario: Non-owner refusal   # R:ERR_AUTH_FORBIDDEN (403)
  Given a confirm the server rejects with 403 ERR_AUTH_FORBIDDEN
  When the response returns
  Then a calm "you don't have permission…" message shows
  And the claim and seal are unchanged

Scenario: Claim not found   # R:ERR_DOMAIN_CLAIM_NOT_FOUND (404)
  Given a confirm the server rejects with 404 ERR_DOMAIN_CLAIM_NOT_FOUND
  When the response returns
  Then a calm "couldn't find this domain claim… refresh" message shows
  And no seal flips

Scenario: Resend refused on a generic domain   # R:ERR_DOMAIN_GENERIC (422)
  Given a resend the server rejects with 422 ERR_DOMAIN_GENERIC
  When the response returns
  Then a calm "public email domains can't be domain-verified…" message shows
  And the segments are not cleared and the claim is unchanged

Scenario: Resend refused for a personal account   # R:ERR_MEMBER_VERIFY_NOT_ELIGIBLE (403)
  Given a resend the server rejects with 403 ERR_MEMBER_VERIFY_NOT_ELIGIBLE
  When the response returns
  Then a calm "member verification isn't available for personal accounts" message shows
  And the segments are not cleared and the claim is unchanged

Scenario: Unmapped failure falls back calmly   # R:generic fallback
  Given a confirm that fails with an unmapped code or a non-BffError
  When the failure surfaces
  Then a calm generic "something went wrong, please try again" role="status" message shows
  And the segments remain filled and the claim is unchanged

Scenario: Non-digit input is rejected client-side   # R:client-side (no request)
  Given the focused segment
  When the user types a non-digit
  Then the segment is unchanged and NO network request is made
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
FE→BFF CALLS (bffPost prefixes `/api/gw`; the `[...path]` catch-all proxies to the FROZEN gateway
endpoints — cookie-auth, no client-side Authorization header; consumed VERBATIM, never re-shaped):

  POST /admin/domain-claims/{claim_id}/member-verify   body: { code: string }   # the 6 assembled digits ONLY
    200 -> DomainClaimListItem   # member_verified_at set; status stays "pending"
    4xx -> { code: "ERR_MEMBER_VERIFY_CODE_INVALID"(400) | "ERR_MEMBER_VERIFY_CODE_EXPIRED"(410)
             | "ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS"(429) | "ERR_RATE_LIMITED"(429)
             | "ERR_DOMAIN_CLAIM_NOT_PENDING"(409) | "ERR_MEMBER_VERIFY_DOMAIN_MISMATCH"(403)
             | "ERR_AUTH_FORBIDDEN"(403) | "ERR_DOMAIN_CLAIM_NOT_FOUND"(404) }

  POST /admin/domain-claims/{claim_id}/member-verify/resend   body: {}
    200 -> DomainClaimListItem   # fresh code emailed to the caller's own account email
    4xx -> { code: "ERR_DOMAIN_GENERIC"(422) | "ERR_MEMBER_VERIFY_NOT_ELIGIBLE"(403)
             | "ERR_MEMBER_VERIFY_DOMAIN_MISMATCH"(403) | "ERR_AUTH_FORBIDDEN"(403)
             | "ERR_DOMAIN_CLAIM_NOT_PENDING"(409) | "ERR_DOMAIN_CLAIM_NOT_FOUND"(404)
             | "ERR_RATE_LIMITED"(429) }

FROZEN SHAPES this task fixes (dashboard-side):

1) DomainSealState union (DomainStatusSeal.tsx) — grows ONE arm:
     "verified" | "member-verified" | "pending" | "expired"

2) sealState(claim, now) inputs + precedence — DomainClaimSealInput gains one ADDITIVE field:
     interface DomainClaimSealInput { status: string; expires_at: string; member_verified_at: string | null }
   Ordered derivation (first match wins):
     status === "verified"                          -> "verified"        (Owner; wins over all)
     member_verified_at != null                     -> "member-verified" (⚠ wins over expired + pending)
     new Date(expires_at) > now                     -> "pending"
     otherwise (incl. unparseable expires_at → NaN) -> "expired"

3) Seal rendering per state (label VERBATIM for the 3 frozen arms — additive-only):
     verified        -> Badge variant="success", Lock icon, "Verified"        + sr-only ownership assertion
     member-verified -> Badge variant="accent"/azure, User/person icon, "Member-verified" + sr-only membership assertion
     pending         -> Badge variant="warning", "Pending DNS"
     expired         -> Badge variant="destructive", "Expired"

4) Error-code → calm message map (role="status" always; keyed off bffCode(err); status/generic fallback):
     ERR_MEMBER_VERIFY_CODE_INVALID       -> "That code doesn't match. Double-check the 6 digits from your email and try again."
     ERR_MEMBER_VERIFY_CODE_EXPIRED       -> "This code has expired. Codes are valid for a short window — request a fresh one."   (+Resend)
     ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS  -> "Too many tries on this code. For your security we've paused it — send a new code to start over."   (+Resend)
     ERR_RATE_LIMITED                     -> "You're going a little fast. Please wait a moment, then try again."   (self-contained; NO countdown)
     ERR_DOMAIN_CLAIM_NOT_PENDING         -> "This domain is already verified — no code needed."
     ERR_MEMBER_VERIFY_DOMAIN_MISMATCH    -> "This code was sent to a different email domain than this claim. It can only confirm the domain of your own work email."
     ERR_AUTH_FORBIDDEN                   -> "You don't have permission to confirm this domain."
     ERR_DOMAIN_CLAIM_NOT_FOUND           -> "We couldn't find this domain claim — it may have been removed. Refresh and try again."
     ERR_DOMAIN_GENERIC                   -> "Public email domains (like gmail.com) can't be domain-verified. Use your work email."
     ERR_MEMBER_VERIFY_NOT_ELIGIBLE       -> "Member verification isn't available for personal accounts."
     (unmapped code / non-BffError)       -> "Something went wrong. Please try again."
   Retryable errors PRESERVE the six segments; only a resend 200 clears them.

5) OTP input behavior contract (6 segments):
     - each segment: one char, inputmode="numeric", digits only (non-digit keypress ignored, no advance)
     - digit entry auto-advances focus to the next segment
     - Backspace on empty → focus previous + clear it; on filled → clear in place
     - paste anywhere → strip non-digits, fill L→R with first ≤6 digits; >6 → first 6; <6 → fill available + focus first empty; 0 digits → no-op
     - container role="group" aria-label="6-digit confirmation code"
     - "Confirm" is an explicit button, disabled until all six filled; NO submit-on-6th-digit

6) Cache update (single seal-flip idiom): applyMemberVerified(item: DomainClaimListItem) patches the cached
   claim's member_verified_at from the 200 body (status stays "pending") — parallels the frozen applyVerified.
   DomainClaimListItem gains `member_verified_at: string | null` (ADDITIVE; frozen fields untouched).

7) OnboardingChecklist Step addition:
     id: "confirm_domain"  (Step.id union grows one arm)
     label: "Confirm your work email domain"
     href: "/app/settings?tab=domains"   (deep-link)
     visibility: role === "owner" (role-gated; claims query enabled only for owner)
     complete: claims.some(c => c.member_verified_at != null || c.status === "verified")

8) Component render per rung state (Domains card claim row):
     verified / member-verified -> code block COLLAPSED (DNS card still shown for the Owner climb)
     pending / expired (member_verified_at == null) -> code block EXPANDED above the DNS challenge card
```

Glossary deltas: none new — reuses the FROZEN backend term `member-verified` (member-verified-recognition §3: a rung-1 trust marker on a claim proving mailbox control, distinct from DNS-proven "verified"). UI name for the extended DomainStatusSeal is the "rung-aware climb seal" (presentational alias, not a domain term).

Least-sure flag surfaced at freeze: [contract] the sealState PRECEDENCE that ranks `member-verified` above the client-derived `expired` state (item 2) — an elapsed DNS `expires_at` window must not visually downgrade a durable, server-persisted member-verification. RESOLVED at freeze: Tin ratified member-verified-wins (2026-07-20).

Status: FROZEN @ v1 — approved by Tin Dang, 2026-07-20. DECIDED at freeze: (1) SEAL LABELS — keep the three existing labels VERBATIM ("Verified"/"Pending DNS"/"Expired") + ADD only the "Member-verified" arm (additive-only; the frozen domain-claims-console seal assertions stay untouched; the wireframe's "Owner-verified" relabel is dropped as a non-spec wireframe detail). (2) SEAL PRECEDENCE — member-verified WINS over an elapsed DNS window (order: verified → member-verified → pending → expired). Both match the drafted §1 M2/M3 + §3 items 2/3 verbatim. UDD wireframe confirmed 2026-07-20 (artifact 162c7e60). Presentation + BFF pass-through; all trust server-side (task 4, 6a75579).
Reported: yes — contract freeze report rendered to Tin (banner/ARC/shape/2 flags/evidence/approve) 2026-07-20; Tin picked both recommended options.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (sealState derivation + the OTP input + the error-map + the onboarding step)
Plan (one test per §2 scenario, 27 total; vitest + @testing-library/react + MSW — mirrors `tests/dns-verify-softeners.test.tsx`; REAL timers; MSW `onUnhandledRequest:"error"`, so every BFF route touched is mocked):
<test_plan>
  SEAL / sealState (pure-fn + render):
  - test_member_verified_seal_arm: member_verified_at set + status 'pending' → label "Member-verified" + person icon + sr-only assertion (M1)
  - test_owner_wins_over_member: status 'verified' + member_verified_at set → "Verified", never "Member-verified" (M2)
  - test_member_wins_over_elapsed_expiry: member_verified_at set + status 'pending' + expires_at past → "Member-verified", never "Expired" (M2 ⚠)
  - test_existing_labels_verbatim: pending (future expiry) → "Pending DNS"; verified → "Verified" (M3, frozen sibling assertions unbroken)
  CODE BLOCK visibility:
  - test_code_block_on_actionable_pending: pending + member_verified_at null → block visible above the DNS card; once member_verified_at set → collapsed, DNS card remains (M4)
  OTP input:
  - test_only_digits_auto_advance: type "4" → seg1 "4" + focus→seg2; non-digit ignored, no advance (M5)
  - test_backspace_steps_back: filled 1-3, focus empty seg4, Backspace → focus seg3 + clears it (M6)
  - test_paste_fills_all: paste "41 92-07" → 4,1,9,2,0,7 (non-digits stripped, first 6); paste "abc" (0 digits) → no-op (M7)
  - test_confirm_disabled_until_full: Confirm disabled while any seg empty; enabled at 6 (M8)
  CONFIRM / SUCCESS:
  - test_confirm_submits_code: 6 filled → click Confirm → bffPost(".../member-verify", {code:"419207"}) (M8)
  - test_success_flips_seal_collapses: 200 with member_verified_at → seal "Member-verified", block collapses, status stays "pending" (M9)
  RESEND:
  - test_resend_rearms_clears: click "Resend code", 200 → calm role="status" "fresh code sent" + all segments cleared (M10)
  SEND-ONLY-CODE:
  - test_no_email_or_domain_input: the block renders NO email/domain input; only the code + resend are sent (M14)
  CALM ERRORS (one per frozen code; each asserts role="status" NOT role="alert", the mapped message, segments preserved on retryable, seal/claim unchanged):
  - test_err_invalid_400 (R INVALID) · test_err_expired_410_offers_resend (R EXPIRED) · test_err_too_many_429_offers_resend (R TOO_MANY) · test_err_rate_limited_429_no_countdown (R RATE_LIMITED, M12) · test_err_not_pending_409 (R NOT_PENDING) · test_err_domain_mismatch_403 (R MISMATCH) · test_err_forbidden_403 (R AUTH_FORBIDDEN) · test_err_not_found_404 (R NOT_FOUND) · test_resend_err_generic_422 (R DOMAIN_GENERIC, segments kept) · test_resend_err_not_eligible_403 (R NOT_ELIGIBLE) · test_unmapped_error_calm_fallback (R fallback, segments kept)
  - test_non_digit_no_request: non-digit keystroke → segment unchanged + NO network request (R client-side)
  ONBOARDING:
  - test_checklist_step_appears_deeplinks: owner sees "Confirm your work email domain" step → href /app/settings?tab=domains; completes when a claim has member_verified_at OR status 'verified' (M13)
  REUSE-UNTOUCHED:
  - test_manual_verify_alert_untouched: the reused DNS card's manual "Verify now" loud role="alert" + auto-poll are byte-unchanged (M15) — assert via the frozen sibling suite staying green (do not duplicate)
</test_plan>

Tests live in: `apps/dashboard/tests/member-verified-code-entry.test.tsx` (+ any default MSW handler additions in `apps/dashboard/tests/mocks/handlers.ts`, the established precedent) · run: `cd apps/dashboard && npx vitest run`. MUST run red before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/settings/` `apps/dashboard/components/overview/` `apps/dashboard/tests/member-verified-code-entry.test.tsx` `apps/dashboard/tests-bff/`
Strategy (ordered batches): 1. `DomainStatusSeal.tsx` — grow `DomainSealState` (+`member-verified`), extend `sealState()` derivation (verified→member-verified→pending→expired) + the new azure/person Badge arm (labels of the 3 frozen arms VERBATIM). 2. A new `OtpInput.tsx` (6 segments; digit-only, auto-advance, backspace-to-prev, paste-strip-and-fill, role=group, controlled value + onChange). 3. A new `MemberVerifyCodeEntry.tsx` (the inline block: OtpInput + Confirm(disabled-until-6) + Resend + the calm error-map + role="status" messages) wired to `bffPost` member-verify/resend. 4. `DomainClaimsSettings.tsx` — render the block on an actionable pending claim above the DNS card; add `member_verified_at` to `DomainClaimListItem`; add `applyMemberVerified` (parallel to `applyVerified`); collapse the block once member/owner-verified. 5. `OnboardingChecklist.tsx` — append the `confirm_domain` Step (owner-gated, deep-link, completion rule). 6. Add any default MSW handler needed so an auto-firing read doesn't break a frozen sibling under `onUnhandledRequest:"error"` (handlers.ts precedent).
Persona (required): generic (frontend/interaction discipline carried by the UDD-confirmed wireframe + the §3 contract + WCAG 1.4.1 icon+label rule)
Spawn isolation: shared-tree (in-place on `feat/domain-onboarding-softening`). REASON: sequential single-stream FE task sharing settings components with the already-committed dns-verify-softeners work on this same branch; a worktree would branch from a stale base and force a net-diff merge (worktree-agent-stale-base) — no parallel sibling to isolate from.
Known-problem fixes: (a) additive `member_verified_at` on `DomainClaimListItem` must not disturb the FROZEN `domain-claims-console.test.tsx` / `dns-verify-softeners.test.tsx` seal + label assertions (keep the 3 labels verbatim). (b) MSW `onUnhandledRequest:"error"` — mock every BFF route the block touches; add an INITIAL default handler for any auto-firing read. (c) REAL timers (no fake-timer precedent in tests/); a `poll`-style injectable seam not needed (the code path is user-submitted, not auto-polled). (d) SSR-safe: any localStorage only in useEffect. (e) the manual "Verify now" loud role="alert" stays byte-untouched (v2 narrowing).
Strategy actually used: <fill at VERIFY>
Safety rule (feature-specific): presentation-only — the FE NEVER collects/sends an email or domain (only the 6-digit code, or an empty resend body); all trust/authorization/rate-limit is enforced server-side (task 4, 6a75579). Retryable errors preserve the entered segments; only a resend 200 clears them.
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; allow-list packages only (no new deps — reuse the shipped Badge/icons/bff-client); ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — new suite 27/27; FULL dashboard 1587/1587 (175 files); tsc --noEmit CLEAN.
- [x] coverage did not decrease — additive new components + 27 new tests; the OTP/sealState/error-map/checklist paths are all exercised.
- [x] no test or contract was altered during build — §0–§3 untouched. ONE frozen sibling reconciled ADDITIVELY: `tests-bff/onboarding-checklist.test.tsx` auto-hide fixture now completes the new 5th step (confirm_domain) — the "full completion → hide" INTENT is preserved, not weakened (the build agent flagged this conflict rather than silently patch it).
- [x] the green was EARNED, not gamed — refute-read EARNED (below); tests assert observable behavior (seal label presence/absence, per-code error TEXT, bffPost call args, OTP interactions), not vacuous.
- [x] concurrency / timing safe — presentation-only; no shared mutable timing. REAL timers (no fake-timer precedent). Confirm is disabled until 6 filled + no submit-on-6th → no double-submit.
- [x] no exposed secrets, injection openings, or unexpected dependencies — FE sends ONLY the code / empty resend body; NO email/domain input; NO new dependency (reused Badge/lucide/bff-client); parameterized BFF calls.
- [x] layering & dependencies follow CONVENTIONS.md — presentation + BFF pass-through; trust server-side (task 4); SSR-safe (localStorage only in useEffect); WCAG 1.4.1 icon+label.
- [x] a person reviewed and approved the change — Tin confirmed the UDD wireframe + froze §3 (both 2026-07-20); architecture-sensitivity auto-gate on clean evidence (no security decision client-side).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
Component green-bar (dashboard): `vitest (ci.yml dashboard job, working-directory: apps/dashboard)` — verify `cd apps/dashboard && npx vitest run` (the new suite + the frozen domain-claims-console + dns-verify-softeners suites for additive drift).
- [x] Member-verified seal states — confirmed by `test_member_verified_seal_arm` / `test_owner_wins_over_member` / `test_member_wins_over_elapsed_expiry` (the ratified precedence).
- [x] The 3 existing seal labels render EXACTLY "Verified"/"Pending DNS"/"Expired" — confirmed by the frozen domain-claims-console + dns-verify-softeners suites passing unchanged in the full run.
- [x] OTP block: digit-only, auto-advance, backspace-to-prev, paste-fills-6 (strips non-digits), Confirm disabled-until-6, submits `{code}` to `.../member-verify` — confirmed by the 4 OTP/confirm tests.
- [x] 200 flips the seal to Member-verified + collapses the block, status stays pending — confirmed by `test_success_flips_seal_collapses`.
- [x] Every frozen error code → a distinct CALM `role="status"` message (never `role="alert"`), segments preserved on retryable, 429 promises no countdown — confirmed by the 11 error tests.
- [x] FE renders NO email/domain input, sends only code / empty resend — confirmed by `test_no_email_or_domain_input`.
- [x] Onboarding checklist gains the owner "Confirm your work email domain" step deep-linking to /app/settings?tab=domains, completing on member_verified_at OR verified — confirmed by `test_checklist_step_appears_deeplinks`.
- [x] `git diff` additive; the manual "Verify now" role="alert" + useVerifyPoll auto-poll byte-unchanged; the 3 seal labels not removed — confirmed by grep on the diff (no removal of those lines; deletions are all union/interface/import/label extensions).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — DomainSealState (+member-verified) & sealState used by DomainStatusSeal; OtpInput used by MemberVerifyCodeEntry; MemberVerifyCodeEntry rendered by DomainClaimsSettings on an actionable pending claim; applyMemberVerified patches the query cache; the confirm_domain Step + its domain-claims query wired in OnboardingChecklist — all exercised by the 27 tests + the full render suite.
- [x] DEAD-CODE (code) — no orphan; every new symbol (OtpInput, MemberVerifyCodeEntry, the member-verified seal arm, applyMemberVerified, the confirm_domain Step, the error map) is referenced + tested.
- [x] SEMANTIC — read the seal derivation, the OTP handlers, the error→message map, the checklist step, and the DomainClaimsSettings diff IN FULL; confirmed additive + faithful to §3.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 cites resolves — DomainStatusSeal/sealState (+member arm), DomainClaimListItem (+member_verified_at), applyMemberVerified, bffPost member-verify/resend, OnboardingChecklist Step, DomainClaimsSettings inline block — all present in the current tree (tsc clean).
- [x] no anchor moved/renamed since Ground SHA `6a75579`.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: self (orchestrator diff-read + suite runs) · adversarially checked: no vacuous asserts (46 substance markers; error tests inject a specific problem-code and assert the mapped TEXT + role="status"; the M2 tests assert BOTH presence of one label AND absence of the other; paste/backspace/auto-advance assert focus + value moves; the confirm test asserts the exact bffPost path + body). The single frozen-sibling change is a faithful additive fixture reconciliation (completes the new 5th step), not a weakened assertion.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: self (orchestrator)
1. Security: CLEAR — presentation-only; FE sends only the code (server enforces all trust/authz/rate-limit, task 4); no secret handled client-side; no new dep/injection surface.
2. Concurrency: CLEAR — no shared mutable timing; Confirm disabled-until-6 + no submit-on-6th avoids double-submit; real timers.
3. Architecture: CLEAR — additive-only (frozen labels/alert/useVerifyPoll untouched; the 3 frozen sibling suites green); hexagonal FE layering; the one sibling reconciliation preserves intent.
Verdict: PASS
Residue: none material. §7 note: the confirm_domain step now counts toward the onboarding auto-hide (owner tenants without a domain claim keep seeing the dismissible checklist — intended nudge).
Binding: advisory — architecture (auto-gate on clean evidence)

### GATE RECORD
Reported: yes — the ARC + evidence recorded here; UDD wireframe + §3 freeze already Tin-approved 2026-07-20.
Outcome: PASS — auto-resolved on clean evidence (architecture sensitivity): 27/27 new + 1587/1587 full dashboard + tsc clean; additive-only; refute-read EARNED; 3-lens CLEAR.
component: dashboard · expected green-bar: vitest (ci.yml dashboard job, working-directory: apps/dashboard) · verify: cd apps/dashboard && npx vitest run
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (never for a security gap)
Reviewed by: Tin Dang (UDD + contract-freeze approvals) · date: 2026-07-20

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose inline-on-the-Domains-card; rejected modal/route (rejected — a separate route hides the "climb" relationship between Member and Owner) · reuse `useVerifyPoll` for the code (rejected — the code is USER-SUBMITTED, never auto-polled; only its calm-vs-terminal taxonomy shape is borrowed as a mental model)
- [human] freeze — froze §3 @ v1 (approved by Tin Dang, 2026-07-20. DECIDED at freeze: (1) SEAL LABELS — keep the three existing labels VERBATIM ("Verified"/"Pending DNS"/"Expired") + ADD only the "Member-verified" arm (additive-only; the frozen domain-claims-console seal assertions stay untouched; the wireframe's "Owner-verified" relabel is dropped as a non-spec wireframe detail). (2) SEAL PRECEDENCE — member-verified WINS over an elapsed DNS window (order: verified → member-verified → pending → expired). Both match the drafted §1 M2/M3 + §3 items 2/3 verbatim. UDD wireframe confirmed 2026-07-20 (artifact 162c7e60). Presentation + BFF pass-through; all trust server-side (task 4, 6a75579).)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by Tin Dang (UDD + contract-freeze approvals))

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

