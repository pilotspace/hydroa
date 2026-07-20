# MILESTONE: Domain onboarding softening: progressive trust ladder (soft member-verified rung + invite-by-domain + DNS-flow softeners)

goal: A new admin can start using their workspace and invite their team by verified email domain the moment they sign up — without first completing DNS-TXT — while automatic stranger-join stays strictly gated on DNS-verified domain ownership
rationale: new milestone (intake 2026-07-19, Tin-confirmed) — a follow-on, purely ADDITIVE onboarding-UX
  enhancement to the shipped-and-archived `enterprise-domain-onboarding` line (PR #80). A UX study found
  DNS-TXT is forced as step ONE, an onboarding cliff for the new SMB/founder admin (no DNS access yet,
  propagation whiplash, jargon, zero value until verified) though it fits the enterprise IT admin. Tin
  approved the "progressive trust ladder" direction, **Option A locked**: the soft rung enables
  invite-by-domain only; AUTO-JOIN stays strictly DNS-gated (the frozen trust model is untouched). Not a
  change-request — it never modifies the frozen auto-join semantics; it adds gentler first rungs on top.
  See the UX report: https://claude.ai/code/artifact/f227dc3f-f8fe-4ab5-b0dd-0eede700a6f9
stage: production · status: active · created: 2026-07-19T16:18:08+00:00
release: pending

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
- **Soften the STRONG (DNS-TXT) rung.** Split across 3 tasks (Tin 2026-07-19, after choosing the
  backend-backed variants of both notify + registrar deep-link):
  - `dns-verify-softeners` (dashboard, architecture): auto-poll the DNS record every ~30s and flip
    pending→verified automatically (no manual "verify now" retry that fails on propagation); a "not the
    DNS owner?" one-click hand-off; copy host+value as one block; a "verify later" that never blocks the
    session; a calm "still checking" reframe of not-yet-propagated (400/503) so it never reads as failure;
    the "notify me when it's live" opt-in UI + the registrar deep-link DISPLAY — CONSUMING the two backend
    contracts below.
  - `domain-verify-notify` (gateway, **SECURITY**): the "email me when it's live" backend — an opt-in on a
    pending claim + a background scheduler that re-checks the DNS-TXT record (reusing the FROZEN fail-closed
    `DnsTxtResolver`), and on a match auto-verifies the claim + sends a transactional email. Background
    auto-verify runs with NO human present → the security review (fail-closed DNS, idempotent single email,
    no email-injection/spam vector, no weakening of the DNS-TXT proof) lives here, isolated.
  - `registrar-hint` (gateway, architecture): infer the registrar from the domain's nameservers (a new
    NS-record lookup) and return a deep-link hint; degrade gracefully to a static provider list on lookup
    failure. Consumed by the dashboard deep-link display.
- **Rung-1 "member-verified" recognition** at signup/first-run: surface that the new admin's own email
  domain is recognized (their mailbox was already proven at signup) — "acme.com recognized; you can invite
  your team now. Verify ownership to enable automatic join." DERIVED where possible (a signup-email/domain
  match), not a new persisted status unless a task proves it load-bearing.
- **Invite-by-domain**: an admin-INITIATED bulk invite ("invite anyone @acme.com") extending the shipped
  team-member-invite issuance surface, plus its console affordance. Available once the domain is
  member-verified (rung 1) — no DNS required, because the admin explicitly initiates it.
- The UDD design loop for the console + onboarding surfaces touched (tasks 2 & 3).
Out:
- **AUTO-JOIN semantics — FROZEN, untouched.** Strangers auto-joining by email domain still requires a
  DNS-verified (rung 2) claim; `resolve_verified_tenant` (status='verified') is not modified.
- **Option B (email-domain auto-join toggle) — REJECTED** by Tin (email domain alone ≠ ownership).
- New SSO/SAML/OIDC/SCIM protocol work (shipped, unchanged).
- Changing the `ClaimStatus` DB shape (pending|verified) unless a task proves it load-bearing.
- Making `public_signup_enabled` per-tenant (a separate residual gap).

> UI/UX in scope? YES — tasks 2 & 3 run the UDD loop. IA: the domain-claims console gains rung-aware
> status (member-verified vs owner-verified vs pending DNS) + the invite-by-domain affordance; the
> signup/first-run flow gains the "domain recognized" recognition moment. Interaction: auto-poll status
> that resolves itself; "verify later" + hand-off; one-click invite-by-domain with a confirm.
> Signature element: a **rung-aware trust seal** — extend the existing InvoiceStatusSeal/DomainStatusSeal
> idiom to show the CLIMB (member-verified → owner-verified/"sealed"), not a flat badge. WCAG AA floor.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **The trust ladder has 3 rungs; auto-join never leaves the top (DNS) rung.** Rung 0 unclaimed
  (invite-only) · rung 1 member-verified (SOFT, admin controls a mailbox @domain — proven at signup) ·
  rung 2 owner-verified (DNS-TXT, the only rung that turns on auto-join).
- **Soft rung = "I'm a real employee here", NOT "I own this domain".** It grants only admin-INITIATED
  membership (explicit or bulk invite), never automatic stranger join. This is the whole security argument.
- New GLOSSARY terms — **member-verified** (rung 1: the admin holds a proven mailbox at the domain) ·
  **invite-by-domain** (an admin-initiated bulk invite to everyone at a member-verified domain) ·
  **owner-verified** (rung 2: DNS-TXT-proven domain ownership; the auto-join gate).

### UDD design decisions — CONFIRMED by Tin 2026-07-19 (wireframe: https://claude.ai/code/artifact/46b0d3c6-1d66-474a-8097-d8c43e885636)
- **D1 · rung-aware trust seal (signature element):** extend the frozen DomainStatusSeal into a 3-state
  climb — Member-verified (azure + person icon) → Owner-verified (success + lock, "sealed") → Pending
  (warning). Icon+label carry meaning, never color alone (WCAG 1.4.1).
- **D2 · member-verified is PERSISTED (Tin overrode the "derived" recommendation).** It is stored, not
  read-time-derived. Owning task `member-verified-recognition` decides the exact shape at its CONTRACT —
  LEANING to an ADDITIVE nullable `member_verified_at` column on `tenant_domain_claims` (purely additive;
  leaves the FROZEN `ClaimStatus` pending|verified enum + its CheckConstraint + the partial-unique-index
  UNTOUCHED; the rung seal derives from (status, member_verified_at)). Extending the ClaimStatus enum is
  the alternative but disturbs frozen shape — avoid unless proven necessary.
- **D3 · invite-by-domain = a domain-restricted shareable LINK** (Tin-confirmed): admin-initiated,
  @domain-scoped, revocable; only an @domain email can redeem it → joins as MEMBER. Domain restriction
  enforced SERVER-SIDE. Never auto-join. Extends the frozen member-invite-issuance surface.
- **D4 · auto-poll:** ~30s cadence, ceiling ~15 min (or tab-blur), manual "Check now" fallback. The
  client auto-poll itself is presentation-only — verification semantics unchanged (the seal still flips
  ONLY on a 200 verify success; a 400/503 during auto-poll is a calm "still checking", never the seal or
  a red alert). **UPDATE 2026-07-19 (Tin):** the "email me when it's live" path is now BACKEND-backed and
  split into its own task `domain-verify-notify` (a scheduler that auto-verifies opted-in claims + emails),
  and the registrar deep-link is nameserver-INFERRED via its own task `registrar-hint` (both were expanded
  from the original client-only sketch). The dashboard task consumes both.

## Shared / risky contracts (freeze these first)
- The **domain-verify-notify contract** (opt-in shape on a claim + the scheduler's re-check/auto-verify/email
  behavior + the fail-closed + idempotency + no-injection safety rules) → owning task `domain-verify-notify`
  (SECURITY). Its opt-in request/response shape is CONSUMED by the dashboard `dns-verify-softeners` notify UI —
  freeze it before the dashboard consumes it.
- The **registrar-hint contract** (domain → {registrar, deep_link_url} via NS lookup; graceful-degrade shape) →
  owning task `registrar-hint`. CONSUMED by the dashboard deep-link display — freeze before the dashboard consumes.
- The **rung-derivation contract** (given a tenant + a domain, which rung — and what unlocks at each) →
  owning task `member-verified-recognition` (consumed by the console rung-seal + invite-by-domain gate).
- The **invite-by-domain issuance shape** (request → bulk invites, idempotency, rung-1 precondition,
  rate-limit) → owning task `invite-by-domain` (extends the frozen member-invite-issuance surface).

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] domain-verify-notify          depends-on: none    — **(gateway · SECURITY)** the "email me when it's
  live" backend: an opt-in on a pending claim + a background scheduler that re-checks the DNS-TXT record
  (reusing the FROZEN fail-closed `DnsTxtResolver`) and, on a match, auto-verifies the claim + sends ONE
  transactional email (extends the frozen `EmailSender`/`send_email` seam). Freeze first — its opt-in shape
  is consumed by `dns-verify-softeners`.
- [ ] registrar-hint                depends-on: none    — **(gateway)** infer the registrar from the domain's
  nameservers (new NS-record lookup) → return {registrar, deep_link_url}; degrade to a static provider list on
  lookup failure. Freeze first — its response shape is consumed by `dns-verify-softeners`.
- [ ] dns-verify-softeners          depends-on: domain-verify-notify, registrar-hint — **(dashboard)** soften
  the DNS-TXT flow on the domain-claims console: auto-poll + auto-flip, calm "still checking" reframe of
  not-yet-propagated, one-block copy, "not the DNS owner?" hand-off, "verify later", the notify-opt-in UI +
  the registrar deep-link display (consuming the two backend contracts). Presentation + a bounded client
  poll; no change to verification semantics. UDD polish.
- [ ] member-verified-recognition   depends-on: none    — Derive + surface rung-1 "member-verified" at
  signup/first-run ("your domain is recognized") and expose the rung on the console seal. Owns the
  rung-derivation contract. UDD. (Independent; touches the console but a different surface.)
- [ ] invite-by-domain              depends-on: member-verified-recognition — Admin-initiated bulk
  "invite anyone @domain", gated on rung-1, extending the frozen member-invite-issuance surface + a
  console affordance. UDD.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A new admin signing up with a work email sees their domain RECOGNIZED and can invite teammates WITHOUT first completing DNS-TXT   (← member-verified-recognition `6a75579` surfaces the recognition + persists `member_verified_at`; member-verified-code-entry `b1e4608` is the code-entry climb; invite-by-domain(-ui) `71641c5`/`9ee8198` is the DNS-free invite path — all gate PASS)
- [x] An admin can invite-by-domain from the dashboard; each @domain holder joins as a normal member   (← invite-by-domain `71641c5` + invite-by-domain-ui `9ee8198`. **SPEC-DELTA (D3, Tin-confirmed at kickoff):** the mechanism shipped as a domain-restricted *shareable link* — an @domain holder redeems it via a 6-digit mailbox code and joins directly as MEMBER — NOT per-person "pending invites". The observable outcome (an admin invites a whole domain from the dashboard, no DNS) is met; the "each becomes a pending invite" wording is superseded by the link model Tin approved.)
- [x] The DNS-TXT challenge auto-polls and flips pending→verified with NO manual "verify now" retry; not-yet-propagated reads as a calm "still checking", never a red failure; a "verify later" and a "not the DNS owner?" hand-off path both exist   (← dns-verify-softeners `04ed333`. Note v1→v2 narrowing: auto-poll softeners shipped; the manual loud alert was kept verbatim by design.)
- [x] An admin can opt in to "email me when it's live" and receive ONE email when a background re-check verifies the domain — with the DNS-TXT proof unchanged and the send idempotent   (← domain-verify-notify `6fc4620`, SECURITY triple-verified — fail-closed DNS, idempotent single email)
- [x] The challenge shows a registrar deep-link inferred from the domain's nameservers, degrading to a static provider list on lookup failure   (← registrar-hint `16d29f8` backend + dns-verify-softeners `04ed333` display)
- [x] AUTO-JOIN is unchanged: an unverified (rung-0/rung-1) domain never auto-joins a stranger — only rung-2 (DNS-verified) does   (← all tasks preserve this; regression-guarded: invite-by-domain NEVER consults `resolve_verified_tenant`, provisions role FIXED MEMBER, and `resolve_verified_tenant`/ClaimStatus enum/indexes are byte-untouched — confirmed by empty git diff at 6a verify)
- [x] The console shows the rung CLIMB (member-verified → owner-verified) via the extended seal idiom, WCAG AA   (← member-verified-recognition `6a75579` + member-verified-code-entry `b1e4608`; the DomainStatusSeal 3-state climb, icon+label per WCAG 1.4.1)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway (source) : 4 tasks. domain-verify-notify (opt-in + background DNS re-check scheduler auto-verify + ONE transactional email); registrar-hint (NS-lookup → {registrar, deep_link_url}, static-list degrade); member-verified-recognition (additive nullable `member_verified_at` on `tenant_domain_claims`; rung-derivation; SECURITY); invite-by-domain (domain_invite_links + domain_invite_redemptions tables, migration a4f2d9c17b3e; admin mint/list/revoke + public two-step redeem→MEMBER; SECURITY).
- dashboard (source) : 3 tasks. dns-verify-softeners (auto-poll+flip, calm still-checking, verify-later + not-the-DNS-owner hand-off, notify opt-in + registrar deep-link display); member-verified-code-entry (rung-aware seal climb + code-entry screen); invite-by-domain-ui (inline manage panel + public /join/[token] two-phase redeem + auto-login).
- tooling : untouched (state.json is per-task engine bookkeeping only).
- skill / book : untouched.
- tests (drift reconciliation) : tests/guardrails/test_guardrails_core.py table manifest — additive append of the 2 invite-by-domain tables (`b697d58`); surfaced by the pre-close full gateway suite (per-task suites never ran the guardrails file). No assertion weakened.

### Cross-task evidence   (one row per task)
- registrar-hint (`16d29f8`)              : gate=PASS · architecture · NS-inferred deep-link + static-list degrade · residue=none
- domain-verify-notify (`6fc4620`)         : gate=PASS · SECURITY (triple adversarial-verified) · fail-closed DNS re-check, idempotent single email · residue=none
- dns-verify-softeners (`04ed333`)         : gate=PASS · dashboard · auto-poll softeners (v1→v2: soften auto-poll only, manual loud alert kept verbatim) · residue=none
- member-verified-recognition (`6a75579`)  : gate=PASS · SECURITY · additive `member_verified_at`, HMAC hash-at-rest, SELECT…FOR UPDATE cap · residue=none
- member-verified-code-entry (`b1e4608`)   : gate=PASS · dashboard · rung-aware seal climb + code-entry · residue=none
- invite-by-domain (`71641c5`)             : gate=PASS · SECURITY (2 adversarial verifies + code-read; ONE finding — off-domain addr-spec — FIXED at source, not risk-accepted) · 38/38 · residue=none
- invite-by-domain-ui (`9ee8198`)          : gate=PASS · architecture (refute-read EARNED; 3-lens CLEAR) · 43 new green · residue=none
- Pre-merge full suite: gateway 4219 passed (5 fg chunks @-n5/6) + dashboard 1629 passed + 43 new — 1 cross-task-drift casualty (guardrails manifest) found & fixed additively; 1 known dns-verify-softeners tab-visibility timing flake (passes 14/14 in isolation).

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row (all 7 tasks gate=PASS; the EC↔task citations are inline in the Exit-criteria list above). One honest SPEC-DELTA: EC2's mechanism is the D3 shareable-link model (Tin-confirmed at kickoff), not per-person pending invites — surfaced explicitly in the push/PR ask.
- goal: "A new admin can start using their workspace and invite their team by verified email domain the moment they sign up — without first completing DNS-TXT — while automatic stranger-join stays strictly gated on DNS-verified domain ownership." PROVEN: member-verified (rung 1, mailbox-proven at signup) unlocks invite-by-domain with NO DNS, while auto-join still fires ONLY on a rung-2 `status='verified'` claim (`resolve_verified_tenant` byte-untouched) — the whole security argument holds.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] ASK Tin for push + PR authorization (the standing outward-facing gate) — surface the EC2 spec-delta + the drift fix + the known flake.
- [ ] Push `feat/domain-onboarding-softening` and open a PR to `main` from this Close ship-review; Tin reviews + merges (admin-merge if the org-billing 0-step CI blocks — see [[org-billing-0step-ci]]).
- [ ] Bundle into the next release cut with the other releasable milestone (release.md); tag/publish/deploy remain human-run.
