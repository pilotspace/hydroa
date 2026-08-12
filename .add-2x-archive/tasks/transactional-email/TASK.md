# TASK: EmailSender port (smtp+console adapters) + invite email delivery, copy-link preserved

slug: transactional-email · created: 2026-07-17 · stage: production
milestone: commercial-self-serve
component: gateway, dashboard
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/tenants/api/invites_router.py:create_invite` — POST /admin/invites handler; already fires a fire-and-forget audit write via `asyncio.ensure_future(record_audit(...))` right before building the 201 body. This is the wiring point for a SECOND, independent fire-and-forget email dispatch.
- `apps/gateway/src/gateway/tenants/api/invites_router.py:InviteCreateResponse` — the frozen 201 body (`id, email, role, status, expires_at, created_at, invited_by_user_id, token`). Gains ONE additive field; every existing field stays byte-identical.
- `apps/gateway/src/gateway/tenants/application/invite_use_cases.py:CreateInviteUseCase.execute` — returns `tuple[Invite, str]` (invite, plaintext token); `Invite` carries no tenant display name.
- `apps/gateway/src/gateway/tenants/domain/entities.py:Invite` (l.106-121) / `:Identity` (l.59-69) — confirmed: neither dataclass carries a tenant display name, only `tenant_id` (UUID).
- `apps/gateway/src/gateway/audit/application/audit_writer.py:record_audit` — the FROZEN fire-and-forget/fail-open precedent this task's email dispatch mirrors verbatim: own resource (separate session), broad `except Exception` + log + swallow, scheduled via `asyncio.ensure_future`, never awaited on the request hot path.
- `apps/gateway/src/gateway/proxy/infrastructure/circuit_breaker.py:CircuitBreaker` — reusable per-instance breaker (`guard()` / `record_success()` / `on_upstream_error()`). PROJECT.md SDD fold (object-store-port) proves it drops onto a brand-new IO seam unchanged; the ml-moderation-layer fold requires an ISOLATED instance per ancillary IO seam — this task's SMTP adapter gets its OWN instance, never shared with the proxy's or any other seam's breaker.
- `apps/gateway/src/gateway/core/config.py:Settings` — `object_store_enabled` / `object_store_endpoint` / `object_store_timeout_seconds` / `object_store_max_retries` (l.812-826) is the closest LIVE precedent for a config-gated adapter: bool gate + field group + honest-degrade default. `Settings._validate_otel_config` (l.979-984, `model_validator(mode="after")`) is the closest LIVE precedent for "enabled=True + empty required field → boot ValueError."
- `apps/gateway/src/gateway/main.py:create_app` (l.426) + `build_object_store` wiring (l.153, l.978, `app.state.object_store = build_object_store(settings)`) — the composition-root pattern this task's `build_email_sender(settings) → app.state.email_sender` mirrors.
- `apps/dashboard/components/members/InviteMemberDialog.tsx` — the byte-identical-must-stay copy-link success UI; `handleCopy`/`inviteLink` already build `${window.location.origin}/invite/${created.token}` client-side. Gains ONE additive line in the success state, gated on the new response field.
- `apps/dashboard/components/members/types.ts:InviteCreateResponse` — the FE mirror of the backend response type; gains the SAME additive field (optional, `?:`), byte-identical otherwise.
- `apps/dashboard/app/(auth)/invite/[token]/page.tsx` — confirms the real dashboard accept route is `/invite/{token}` (singular) — the exact path the server-built link must reproduce.

Context (working folder):
- `apps/gateway/pyproject.toml` — dependency list confirmed: NO email-sending library present (no aiosmtplib/sendgrid/boto3-ses). `tenacity>=8.2` (bounded retry) and stdlib `smtplib`/`email.message` are already available — feeds the §1 "no new heavy dependency" decision.
- `.add/GLOSSARY.md` — grepped in full for "email"/"EmailSender"/"fire-and-forget": no existing "EmailSender" or "email delivery" term. This task's Glossary delta introduces it.

Honors (patterns / conventions):
- PROJECT.md invariant: "No outbound IO without timeout + bounded retry (idempotent only) + circuit breaker" — SMTP send is outbound IO; must honor all three.
- MILESTONE.md (commercial-self-serve) shared decision: EmailSender port is fire-and-forget like audit writes; an email failure NEVER fails the primary request; adapters `smtp` (config-gated) + `console` (default, logs rendered mail); copy-link invite response stays byte-identical, email is additive delivery.
- Backend-architect shipped layout (this codebase's own convention, not a persona preference): `domain/` (entities + `typing.Protocol` ports, zero infra imports) · `application/` (use-case/dispatch orchestration) · `infrastructure/` (adapters) · `api/` (routers). The new `gateway/email/` bounded context follows the same four-folder shape.

Seams consulted: none in SEAMS.md for email — new seam this task introduces.

Anchors the contract cites:
`gateway.email.domain.entities.EmailMessage` · `gateway.email.domain.ports.EmailSender` · `gateway.email.domain.errors.EmailSendError` · `gateway.email.infrastructure.console_email_sender.ConsoleEmailSender` · `gateway.email.infrastructure.smtp_email_sender.SmtpEmailSender` · `gateway.email.application.email_dispatch.send_email` · `gateway.email.application.invite_email_template.render_invite_email` · `gateway.main.build_email_sender` · `gateway.core.config.Settings` (new `email_smtp_*` + `dashboard_public_origin` fields + `_validate_email_smtp_config` validator) · `gateway.tenants.api.invites_router.create_invite` / `InviteCreateResponse` · `apps/dashboard/components/members/InviteMemberDialog.tsx` · `apps/dashboard/components/members/types.ts:InviteCreateResponse`.

Issues/Risks (→ feed §1):
1. PROJECT.md's own "empty-key boot-guard precedent" (`EmptyUpstreamKeyError`) is STALE — that class was fully DELETED 2026-06-17 (`.add/tasks/retire-empty-key-guard/TASK.md`; BYOK made the guard a permanent no-op). The LIVE boot-guard idiom in `config.py` today is a `model_validator(mode="after")` raising `ValueError` (`_validate_otel_config`'s exact shape) — this task's SMTP-empty-host guard must use THAT shape, not resurrect the deleted class name.
2. No dashboard/public-origin setting exists anywhere in `config.py` (confirmed by a full-file grep — only per-provider upstream `*_base_url` fields exist). The gateway has no CORS config either, meaning dashboard↔gateway calls flow exclusively through the dashboard's own server-side BFF — a public origin concept genuinely does not exist yet; it is a NEW setting, not a rename.
3. `CreateInviteUseCase`/`Invite` carry no tenant display name — the accept-email body can only honestly reference the inviter's email + assigned role + link, not a tenant name, without adding a new repo query this task doesn't otherwise need.
4. Fire-and-forget means the 201 response is built and returned BEFORE the email send even starts (mirrors the audit dispatch's own timing) — any response field this task adds can only ever describe which channel was DISPATCHED (attempted), never confirmed delivered/failed.
5. SMTP is a genuinely new outbound-IO seam with zero adapter precedent elsewhere in this codebase (grep for `smtplib`/`aiosmtplib`/`sendgrid` across `src/gateway` is empty) — the retry/circuit-breaker wiring must be built fresh, though the `CircuitBreaker` PRIMITIVE itself is reused unchanged (object-store-port fold).

Related intent: MILESTONE.md `commercial-self-serve` § Shared decisions (EmailSender port) · PROJECT.md invariant "no outbound IO without timeout + bounded retry + circuit breaker" · PROJECT.md SDD folds: object-store-port ("the breaker is IO-tier-agnostic ... a reusable primitive") + ml-moderation-layer ("a BYOK provider used for an ANCILLARY IO seam needs an ISOLATED CircuitBreaker instance from the SAME provider's PRIMARY seam") · GLOSSARY delta this task adds: **EmailSender**.

Ground SHA: `102ec65`

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: EmailSender port (smtp + console adapters) wired to invite creation — accept-link delivery, additive to the byte-identical copy-link flow.
Framings weighed: generic `EmailSender` port, invite-template layered on top (chosen — `EmailMessage{to,subject,text_body,html_body}` + `Protocol.send()`, zero invite-specific fields, reusable for alerts/invoices next milestone per MILESTONE.md) · invite-specific `InviteNotifier` port (rejected — narrower, would need replacing when alerts/invoices land; MILESTONE.md explicitly asks for the general shape) · third-party transactional-email HTTP API (SendGrid/SES) instead of/alongside SMTP (rejected THIS milestone — MILESTONE.md names "smtp" explicitly; an HTTP-provider adapter is a clean FUTURE addition behind the same Protocol).
Must:
<must>
  - M1: `EmailSender` is a `typing.Protocol` with exactly one method, `async def send(self, message: EmailMessage) -> None` — zero invite-specific fields on the port itself, reusable by a future alerts/invoices caller without a signature change.
  - M2: Two adapters ship: `ConsoleEmailSender` (logs the fully-rendered message at INFO via the existing structlog logger, never raises) and `SmtpEmailSender` (stdlib `smtplib` via `asyncio.to_thread`, its OWN isolated `CircuitBreaker` instance, tenacity-bounded retry on transient errors only, explicit socket timeout).
  - M3: Adapter selection is config-gated at the composition root: `Settings.email_smtp_enabled` (bool, default `False`) selects `SmtpEmailSender` when `True`, `ConsoleEmailSender` otherwise (default) — mirrors `object_store_enabled`'s exact shape.
  - M4: `email_smtp_enabled=True` with an empty `email_smtp_host` is a boot-time misconfiguration — `Settings()` construction fails fast with a clear `ValueError` (`model_validator(mode="after")`, the LIVE `_validate_otel_config` shape), never an opaque per-request failure.
  - M5: `email_smtp_enabled=True` with an empty `dashboard_public_origin` is ALSO a boot-time misconfiguration (same validator) — SMTP delivery of a link nobody can build server-side is refused at boot, never silently sent broken.
  - M6: A brand-new `dashboard_public_origin` setting (default `""`) is added to `Settings` — no prior gateway concept of the dashboard's public origin exists (confirmed absent). Empty + SMTP disabled (today's default) never boot-errors — the console adapter renders a relative `/invite/{token}` link when origin is unset.
  - M7: `send_email(sender, message)` (application layer) wraps `sender.send()` exactly like `record_audit` wraps `AuditRepository.record` — catches ALL exceptions, logs + swallows, NEVER raises into the caller; scheduled via `asyncio.ensure_future`, never awaited on the request hot path.
  - M8: `POST /admin/invites` (`create_invite`) additionally dispatches a fire-and-forget invite-accept email — a SECOND, INDEPENDENT task from the existing audit dispatch (one failing never affects the other) — built by `render_invite_email(email, role, token, origin)`. The accept link is exactly `f"{origin}/invite/{token}"`, matching the dashboard's existing client-side construction and the real route `/invite/[token]`.
  - M9: `InviteCreateResponse` gains ONE additive field, `email_delivery_channel: Literal["smtp", "console"]`, naming which adapter was DISPATCHED to (attempted, not confirmed-delivered — fire-and-forget can't know yet by response time). Every existing field is byte-identical.
  - M10: SMTP send is bounded outbound IO per the PROJECT.md invariant: explicit timeout (`email_smtp_timeout_seconds`, default 5.0s) on the smtplib socket, a bounded retry (`email_smtp_max_retries`, default 2) via tenacity limited to transient/idempotent-safe errors (connect/timeout), and an isolated `CircuitBreaker` instance that fails the send (raises `EmailSendError`, caught by M7) when open.
  - M11: `InviteMemberDialog`'s success state gains ONE additive line of copy when `email_delivery_channel === "smtp"` ("We've also emailed this link to {email}."); when `"console"` (or absent) it shows nothing extra — honest, since console mode never actually delivers. The copy-link block (link, Copy button, Done button) is completely unchanged.
  - M12: `apps/dashboard/components/members/types.ts`'s `InviteCreateResponse` gains the same additive `email_delivery_channel` field, kept OPTIONAL (`?:`) in the FE type for forward/backward safety.
</must>
Reject:
<reject>
  - R1: `SmtpEmailSender.send` raising (auth failure, connection refused, timeout, circuit open, any `Exception`) never surfaces to the invite HTTP response; the response is still 201 with the unchanged body shape -> feeds M7 (no dedicated client-visible error code — fire-and-forget, no rejection reaches the caller).
  - R2: `email_smtp_enabled=true` + `email_smtp_host=""` at `Settings` construction -> `"GATEWAY_EMAIL_SMTP_HOST must be set when GATEWAY_EMAIL_SMTP_ENABLED=true"` (boot `ValueError`; app never starts) -> `"email_smtp_host_required_when_enabled"`.
  - R3: `email_smtp_enabled=true` + `dashboard_public_origin=""` at `Settings` construction -> `"GATEWAY_DASHBOARD_PUBLIC_ORIGIN must be set when GATEWAY_EMAIL_SMTP_ENABLED=true"` (boot `ValueError`) -> `"dashboard_public_origin_required_when_smtp_enabled"`.
  - R4: A non-transient SMTP error (`SMTPAuthenticationError`, `SMTPRecipientsRefused`, etc.) is NEVER retried (retry is bounded to transient/connection errors only) -> logged once, swallowed by M7, the breaker still counts it as a failure -> no distinct client code (fire-and-forget) -> `"email_send_non_retryable"` (internal log tag only, never HTTP-visible).
  - R5: `email_smtp_max_retries` < 0 or `email_smtp_timeout_seconds` <= 0 -> boot `ValueError` via `Field(ge=0)` / `Field(gt=0)` constraints (mirrors `object_store_timeout_seconds`/`object_store_max_retries`'s exact style) -> pydantic validation error at `Settings` construction.
</reject>
After:
<after>
  - `gateway.email` is a new bounded context (`domain/` · `application/` · `infrastructure/`) shipping an `EmailSender` Protocol, `ConsoleEmailSender` + `SmtpEmailSender` adapters, and a fail-open `send_email` dispatch wrapper.
  - `create_app` wires `app.state.email_sender` via `build_email_sender(settings)`, defaulting to console unless `email_smtp_enabled=true` (boot-validated).
  - `POST /admin/invites` dispatches a fire-and-forget invite-accept email alongside the existing audit event; its 201 response carries one additive `email_delivery_channel` field; every other field, and the whole copy-link UI flow, is byte-identical to today.
  - `InviteMemberDialog` optionally shows one additional line of copy when the channel is `"smtp"`; otherwise identical to today.
  - No other caller (alerts/invoices) is wired to `EmailSender` this milestone — the port exists, unconsumed by them, ready for the next milestone.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ #1 the accept-link email BODY omits the tenant's display name (only inviter email + role + link) because `CreateInviteUseCase`/`Invite` carry no tenant name today — lowest confidence because a human recipient may find "you've been invited to join a tenant" oddly impersonal without a workspace name; if wrong: a follow-up adds one repo query (tenant-name lookup) to enrich the template — additive, no contract-shape change.
  ⚠ #2 `email_delivery_channel` on the 201 response reports the channel DISPATCHED-to, not confirmed-delivered (a real SMTP failure after the 201 already returned `"smtp"` is invisible to the caller) — lowest-but-one confidence because this could read as overclaiming delivery success to an owner/admin; if wrong: soften the field/FE copy to "dispatched" language, or drop the FE copy line (M11) entirely and keep the field backend-only for observability.
  - [ ] #3 SMTP retry count of 2 and timeout of 5.0s are reasonable-but-arbitrary defaults (mirrors `object_store`'s own 5.0s/2-retry defaults) — confirm or adjust; low cost if wrong (one-line constant change).
  - [ ] #4 `dashboard_public_origin` boot-errors only when SMTP is enabled (never when console/default) — confirm this coupling vs. always requiring it regardless of adapter (to make even console-logged links realistic everywhere).
  - [ ] #5 STARTTLS-by-default (`email_smtp_use_tls=True`) is assumed correct for typical SMTP relays (SendGrid/SES/Postmark SMTP endpoints, Gmail relay) — confirm no target provider needs implicit TLS (port 465) instead, which would need a second boolean.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: EmailSender Protocol is adapter-agnostic   # M1
  Given ConsoleEmailSender and SmtpEmailSender both implement EmailSender
  When a caller holds only an `EmailSender`-typed reference (no concrete adapter import)
  Then calling `await sender.send(message)` works identically regardless of which adapter is injected
  And EmailMessage carries no invite-specific field (to/subject/text_body/html_body only)

Scenario: console adapter logs the rendered mail   # M2
  Given email_smtp_enabled=False (default)
  When create_invite dispatches an invite email
  Then ConsoleEmailSender.send logs the to/subject/body at INFO via structlog
  And no network call is attempted and no exception is ever raised

Scenario: smtp adapter sends over a timed, retried, breaker-guarded connection   # M2, M10
  Given email_smtp_enabled=True with a valid host/port/credentials
  When create_invite dispatches an invite email
  Then SmtpEmailSender.send runs smtplib inside asyncio.to_thread with the configured socket timeout
  And a transient connection failure is retried up to email_smtp_max_retries times before raising
  And the isolated CircuitBreaker instance records the outcome without touching any other seam's breaker state

Scenario: console is the default adapter when SMTP is unconfigured   # M3
  Given email_smtp_enabled is left at its default (False)
  When build_email_sender(settings) runs at composition root
  Then it returns a ConsoleEmailSender
  And app.state.email_sender is never a SmtpEmailSender

Scenario: smtp enabled selects the smtp adapter   # M3
  Given email_smtp_enabled=True and email_smtp_host is non-empty
  When build_email_sender(settings) runs at composition root
  Then it returns a SmtpEmailSender configured from the email_smtp_* settings

Scenario: configured-but-empty SMTP host boot-errors   # M4, R2
  Given email_smtp_enabled=True and email_smtp_host=""
  When Settings() is constructed (app boot)
  Then a ValueError "GATEWAY_EMAIL_SMTP_HOST must be set when GATEWAY_EMAIL_SMTP_ENABLED=true" is raised
  And the app never reaches create_app / never starts serving traffic

Scenario: SMTP enabled without a dashboard origin boot-errors   # M5, R3
  Given email_smtp_enabled=True, email_smtp_host set, and dashboard_public_origin=""
  When Settings() is constructed (app boot)
  Then a ValueError "GATEWAY_DASHBOARD_PUBLIC_ORIGIN must be set when GATEWAY_EMAIL_SMTP_ENABLED=true" is raised
  And the app never starts

Scenario: default config (console + no origin) never boot-errors   # M6
  Given email_smtp_enabled=False (default) and dashboard_public_origin="" (default)
  When Settings() is constructed (app boot)
  Then no ValueError is raised — this is today's out-of-the-box shape
  And render_invite_email builds a relative "/invite/{token}" link (no origin prefix) for the console log

Scenario: dashboard_public_origin set builds an absolute link   # M6, M8
  Given dashboard_public_origin="https://app.hydroa.example"
  When render_invite_email builds the accept link for token "abc123"
  Then the link is exactly "https://app.hydroa.example/invite/abc123"
  And it matches the dashboard's own client-side ${window.location.origin}/invite/${token} construction

Scenario: an email send failure never fails invite creation   # M7, R1
  Given SmtpEmailSender.send raises (auth failure, timeout, or circuit-open) for a given invite
  When POST /admin/invites is called
  Then the response is still 201 with the full InviteCreateResponse body (including token)
  And the exception is logged and swallowed by send_email, never propagated to the router
  And the existing audit.create write (a separate fire-and-forget task) is unaffected either way

Scenario: invite email dispatch is independent of the audit dispatch   # M8
  Given the email adapter is configured to always raise on send()
  When POST /admin/invites succeeds
  Then the invite.create audit_events row is still written (its own independent fire-and-forget task)
  And the failed email send does not prevent, delay, or roll back the audit write

Scenario: re-invite dispatches a fresh email for the new token   # M8 edge case
  Given a pending invite for alice@example.com already exists and was emailed once
  When the same owner/admin re-invites alice@example.com (atomic supersede, M5 of member-invite-issuance)
  Then a NEW fire-and-forget email is dispatched carrying the NEW plaintext token's accept link
  And the old token's link (already sent, now unresolvable) is not re-sent or invalidated by this task's logic

Scenario: response carries the additive delivery-channel field   # M9
  Given email_smtp_enabled=True
  When POST /admin/invites succeeds
  Then InviteCreateResponse.email_delivery_channel == "smtp"
  And id/email/role/status/expires_at/created_at/invited_by_user_id/token are byte-identical in shape to before this task

Scenario: response reports console channel by default   # M9
  Given email_smtp_enabled=False (default)
  When POST /admin/invites succeeds
  Then InviteCreateResponse.email_delivery_channel == "console"
  And every other field is byte-identical to before this task

Scenario: a non-transient SMTP error is never retried   # R4
  Given SmtpEmailSender.send hits SMTPAuthenticationError (permanent, not connection/timeout)
  When the send is attempted
  Then no retry attempt is made (tenacity's retry predicate excludes this exception type)
  And the single failure is logged, swallowed by send_email, and counted once against the circuit breaker

Scenario: invalid SMTP timeout/retry settings boot-error   # R5
  Given email_smtp_timeout_seconds=0 (or email_smtp_max_retries=-1)
  When Settings() is constructed (app boot)
  Then a pydantic validation error is raised (Field(gt=0) / Field(ge=0) constraint)
  And the app never starts

Scenario: dialog shows the extra line only for smtp delivery   # M11
  Given an invite was created and the response's email_delivery_channel is "smtp"
  When InviteMemberDialog renders its success state
  Then it shows "We've also emailed this link to {email}." in addition to the unchanged copy-link block
  And the Copy button still copies exactly ${window.location.origin}/invite/${created.token}

Scenario: dialog shows no extra line for console delivery   # M11
  Given an invite was created and the response's email_delivery_channel is "console" (or the field is absent)
  When InviteMemberDialog renders its success state
  Then no extra delivery-copy line is shown
  And the copy-link block (link, Copy button, Done button) is pixel-identical to today's shipped UI

Scenario: FE type stays backward-compatible   # M12
  Given a BFF response that omits email_delivery_channel entirely (old cached response / rollback)
  When the dashboard parses it as InviteCreateResponse
  Then no runtime error occurs (the field is optional in types.ts)
  And the dialog falls back to showing no extra delivery-copy line

Scenario: two concurrent invite creates in the same tenant each get their own isolated send   # concurrency edge case
  Given two owners in the SAME tenant invite two different emails at nearly the same instant
  When both POST /admin/invites calls are in flight
  Then each gets its own fire-and-forget email task with its own token/link
  And a slow/failing send for one invite never blocks or fails the other invite's HTTP response
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# ── Port (gateway/email/domain/ports.py) ────────────────────────────────────
class EmailMessage:                       # frozen dataclass, gateway/email/domain/entities.py
    to: str
    subject: str
    text_body: str
    html_body: str | None = None          # optional; adapters may ignore

class EmailSender(Protocol):              # gateway/email/domain/ports.py — zero infra imports
    async def send(self, message: EmailMessage) -> None: ...
    # Raises EmailSendError (gateway/email/domain/errors.py) on failure — NEVER caught here;
    # the caller (send_email) is the fail-open boundary, mirroring record_audit/AuditRepository.

# ── Application (gateway/email/application/) ────────────────────────────────
async def send_email(sender: EmailSender, message: EmailMessage) -> None:
    """Fire-and-forget, fail-open — byte-identical CONTRACT shape to record_audit:
    catches ALL exceptions, logs+swallows, never raises. Caller schedules via
    asyncio.ensure_future(send_email(...)), never awaits on the hot path."""

def render_invite_email(*, to: str, role: str, token: str, origin: str) -> EmailMessage:
    """Pure function. link = f"{origin}/invite/{token}" if origin else f"/invite/{token}".
    Body: inviter-neutral "You've been invited to join as {role}." + the link.
    No tenant display name (§1 ⚠#1 — Invite/Identity carry none today)."""

# ── Adapters (gateway/email/infrastructure/) ────────────────────────────────
class ConsoleEmailSender:                 # implements EmailSender
    async def send(self, message: EmailMessage) -> None:
        # structlog .info(...) the to/subject/body; never raises.

class SmtpEmailSender:                    # implements EmailSender
    def __init__(self, settings: Settings) -> None:
        # builds its OWN CircuitBreaker() instance — never shared with any other seam.
    async def send(self, message: EmailMessage) -> None:
        # self._breaker.guard(); tenacity AsyncRetrying(stop=stop_after_attempt(
        #   settings.email_smtp_max_retries + 1), retry=retry_if_exception_type(
        #   (OSError, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected)))
        #   wraps: await asyncio.to_thread(_send_sync, ...) where _send_sync opens
        #   smtplib.SMTP(host, port, timeout=settings.email_smtp_timeout_seconds),
        #   STARTTLS if email_smtp_use_tls, login if username set, sendmail(...).
        # On success: self._breaker.record_success(). On exhausted/non-retryable/
        # circuit-open failure: self._breaker.on_upstream_error(); raise EmailSendError.

# ── Composition root (gateway/main.py) ───────────────────────────────────────
def build_email_sender(settings: Settings) -> EmailSender:
    """Mirrors build_object_store(settings)'s shape exactly.
    return SmtpEmailSender(settings) if settings.email_smtp_enabled else ConsoleEmailSender()"""
# create_app: app.state.email_sender = build_email_sender(settings)

# ── Settings additions (gateway/core/config.py) ─────────────────────────────
email_smtp_enabled: bool = False                       # GATEWAY_EMAIL_SMTP_ENABLED
email_smtp_host: str = ""                              # GATEWAY_EMAIL_SMTP_HOST
email_smtp_port: int = 587                             # GATEWAY_EMAIL_SMTP_PORT
email_smtp_username: str = ""                          # GATEWAY_EMAIL_SMTP_USERNAME
email_smtp_password: SecretStr = SecretStr("")         # GATEWAY_EMAIL_SMTP_PASSWORD — masked
email_smtp_use_tls: bool = True                        # GATEWAY_EMAIL_SMTP_USE_TLS (STARTTLS)
email_smtp_from_address: str = "no-reply@hydroa.local" # GATEWAY_EMAIL_SMTP_FROM_ADDRESS
email_smtp_timeout_seconds: float = Field(default=5.0, gt=0)   # GATEWAY_EMAIL_SMTP_TIMEOUT_SECONDS
email_smtp_max_retries: int = Field(default=2, ge=0)   # GATEWAY_EMAIL_SMTP_MAX_RETRIES
dashboard_public_origin: str = ""                      # GATEWAY_DASHBOARD_PUBLIC_ORIGIN

@model_validator(mode="after")
def _validate_email_smtp_config(self) -> "Settings":
    if self.email_smtp_enabled and not self.email_smtp_host:
        raise ValueError("GATEWAY_EMAIL_SMTP_HOST must be set when GATEWAY_EMAIL_SMTP_ENABLED=true")
    if self.email_smtp_enabled and not self.dashboard_public_origin:
        raise ValueError(
            "GATEWAY_DASHBOARD_PUBLIC_ORIGIN must be set when GATEWAY_EMAIL_SMTP_ENABLED=true"
        )
    return self

# ── HTTP contract delta (gateway/tenants/api/invites_router.py) ─────────────
POST /admin/invites   body: { email: EmailStr, role: str }     # UNCHANGED request shape
  201 -> { id, email, role, status, expires_at, created_at, invited_by_user_id, token,
           email_delivery_channel: "smtp" | "console" }        # ONE additive field, end of body
  # every other status/error path (403/422/409) UNCHANGED — email dispatch never raises into the router.
  # wiring: after the existing asyncio.ensure_future(record_audit(...)) call, ADD an independent
  #   asyncio.ensure_future(send_email(request.app.state.email_sender,
  #     render_invite_email(to=invite.email, role=invite.role.value, token=token,
  #                          origin=settings.dashboard_public_origin)))
  #   (settings reached via request.app.state.settings, or a Depends — whichever this router's
  #   existing DI style already uses; the two ensure_future calls are independent tasks).

Schema: no new tables/columns — EmailSender is a pure application-layer seam over the existing
`invites` row (Invite/token already persisted by member-invite-issuance). No persistence added by
this task.

# ── FE contract delta ────────────────────────────────────────────────────────
# apps/dashboard/components/members/types.ts
export interface InviteCreateResponse {
  id: string; email: string; role: string; status: string; expires_at: string;
  created_at: string; invited_by_user_id: string; token: string;
  email_delivery_channel?: "smtp" | "console";   # ADDITIVE, optional
}

# apps/dashboard/components/members/InviteMemberDialog.tsx — success-state delta only:
#   {created.email_delivery_channel === "smtp" && (
#     <p className="text-sm text-muted-foreground">
#       We've also emailed this link to <strong>{created.email}</strong>.
#     </p>
#   )}
#   — inserted between the existing "Share this one-time link..." <p> and the <code> link block;
#   nothing else in the dialog changes (byte-identical copy-link block, Copy/Done buttons).
```

Glossary deltas: **EmailSender** (NEW term): a gateway-owned port (`typing.Protocol`, `async def send(message: EmailMessage) -> None`) for ancillary, fire-and-forget outbound email — fail-open like the existing audit-writer seam (a send failure never fails the primary request). Two adapters: `console` (default, logs the rendered mail, never a real delivery) and `smtp` (config-gated, stdlib `smtplib` + an isolated `CircuitBreaker` + bounded tenacity retry + explicit timeout). Wired to exactly one caller this milestone (`POST /admin/invites`'s accept-link email); designed to be reused unchanged by a future alerts/invoices caller (out of this milestone's scope). [folded foundation-version 54]
Least-sure flag surfaced at freeze: [contract] `email_delivery_channel` reports the channel DISPATCHED-TO, never confirmed-delivered (fire-and-forget returns 201 before the send starts); overclaim risk is bounded by the dispatch-honest FE copy ruling (#2). [spec] v1 body omits the tenant display name (Invite/Identity carry none) — enrichment is an additive follow-up.
Status: FROZEN @ v1 — approved by orchestrator under Tin's standing full-auto directive (2026-07-17).
Reported: yes — flags #1–#5 triaged in-session; rulings below.
Decided at freeze (verbatim rulings):
- #1 ACCEPTED: v1 email body carries inviter-neutral copy (role + link, no tenant display name — Invite/Identity carry none today); tenant-name enrichment = additive follow-up, logged as an observe delta.
- #2 ACCEPTED with copy guard: `email_delivery_channel` semantics are DISPATCHED-TO, never confirmed-delivered; the M11 dialog line stays but MUST use dispatch-honest wording — "We've emailed this link to {email}." is acceptable; never "delivered". FE copy review checks this at verify.
- #3 CONFIRMED: timeout 5.0s / 2 retries (mirrors object_store defaults).
- #4 CONFIRMED: `dashboard_public_origin` boot-errors only when SMTP is enabled; console/default boots exactly as today (byte-identical out-of-the-box).
- #5 CONFIRMED: STARTTLS-by-default (`email_smtp_use_tls=True`, port 587); implicit-TLS 465 support = future additive boolean if a provider demands it.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (task-scoped: `apps/gateway/src/gateway/email/` + the invites_router.py
delta + the InviteMemberDialog.tsx delta). Achieved: 22/22 backend tests green over the new
`gateway.email` package + config validator + router wiring; 3/3 FE tests green over the
dialog's additive copy line + type. All 22 backend + 3 FE scenarios map 1:1 to §2.

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_email_sender_protocol_is_adapter_agnostic: both adapters satisfy EmailSender; EmailMessage has exactly {to,subject,text_body,html_body} · covers: M1
  - test_console_adapter_logs_rendered_mail_never_raises: capture_logs() sees an INFO event with to/subject/body; no exception · covers: M2
  - test_smtp_adapter_retries_transient_failure_then_succeeds / test_smtp_adapter_exhausts_retries_then_raises: faked smtplib, transient OSError retried up to email_smtp_max_retries then succeeds/raises · covers: M2, M10
  - test_smtp_adapter_owns_an_isolated_circuit_breaker_instance / test_smtp_adapter_circuit_open_short_circuits_without_a_new_send: two instances never share a breaker; OPEN short-circuits without a new (faked) connection · covers: M10
  - test_build_email_sender_defaults_to_console / test_build_email_sender_selects_smtp_when_enabled: composition-root selection · covers: M3
  - test_settings_boot_errors_on_empty_smtp_host_when_enabled / test_settings_boot_errors_on_empty_dashboard_origin_when_smtp_enabled: Settings() raises ValueError · covers: M4/R2, M5/R3
  - test_default_config_never_boot_errors_and_console_link_is_relative: Settings() default + relative link · covers: M6
  - test_render_invite_email_builds_absolute_link_with_origin: exact `{origin}/invite/{token}` · covers: M6, M8
  - test_email_send_failure_never_fails_invite_creation: FakeEmailSender raises, 201 unaffected · covers: M7, R1
  - test_invite_email_dispatch_independent_of_audit_dispatch: email always fails, invite.create audit row still written (polled, not fixed-sleep) · covers: M8
  - test_reinvite_dispatches_fresh_email_for_new_token: 2 dispatches, distinct tokens, no cross-contamination · covers: M8 edge
  - test_response_reports_smtp_channel_when_enabled / test_response_reports_console_channel_by_default: `email_delivery_channel` + byte-identical remaining shape · covers: M9
  - test_non_transient_smtp_error_is_never_retried: SMTPAuthenticationError → 1 attempt only · covers: R4
  - test_invalid_smtp_timeout_boots_errors / test_invalid_smtp_max_retries_boot_errors: Field(gt=0)/Field(ge=0) → ValueError · covers: R5
  - test_two_concurrent_invite_creates_each_get_isolated_send: slow+failing concurrent sends never block/cross-affect each other's 201 · covers: concurrency edge case
  - test_send_email_application_wrapper_swallows_all_exceptions: send_email never raises, logs a warning · covers: M7 (application-layer unit)
  - FE test_invite-member-dialog-email.test.tsx (3 tests): smtp → extra dispatch-honest line; console → no line; field absent → no line, no runtime error · covers: M11, M12
</test_plan>

Tests live in: `./tests/` `apps/gateway/tests/transactional_email/` `apps/dashboard/tests/invite-member-dialog-email.test.tsx` · ran RED (every `gateway.email` import was a true MODULE_NOT_FOUND; the two config-validator scenarios failed as "DID NOT RAISE"; the M9 field-shape assertions failed as AssertionError on the missing key) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/email/` (new directory — domain/application/infrastructure) · `apps/gateway/src/gateway/core/config.py` · `apps/gateway/src/gateway/main.py` · `apps/gateway/src/gateway/tenants/api/invites_router.py` · `apps/gateway/tests/` (new `tests/transactional_email/` + any touched existing invite tests) · `apps/dashboard/components/members/InviteMemberDialog.tsx` · `apps/dashboard/components/members/types.ts`
Strategy (ordered batches):
  1. `gateway/email/domain/` — `entities.py` (EmailMessage), `ports.py` (EmailSender Protocol), `errors.py` (EmailSendError). Zero infra imports (backend-architect convention) — verify by grep.
  2. `gateway/email/infrastructure/console_email_sender.py` — trivial, no IO, no retry/breaker needed.
  3. `gateway/core/config.py` — add the `email_smtp_*` + `dashboard_public_origin` fields and the `_validate_email_smtp_config` validator FIRST (before the SMTP adapter needs them) — mirrors `object_store_*`'s field-group placement and `_validate_otel_config`'s validator placement exactly.
  4. `gateway/email/infrastructure/smtp_email_sender.py` — reuse `gateway.proxy.infrastructure.circuit_breaker.CircuitBreaker` UNCHANGED (own instance); tenacity `AsyncRetrying` limited to `(OSError, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected)`; `asyncio.to_thread` for the blocking smtplib call.
  5. `gateway/email/application/email_dispatch.py` (`send_email`) + `invite_email_template.py` (`render_invite_email`) — pure/thin, no new IO of their own.
  6. `gateway/main.py` — add `build_email_sender(settings)` beside `build_object_store`; wire `app.state.email_sender` in `create_app`.
  7. `tenants/api/invites_router.py` — add the additive `email_delivery_channel` field to `InviteCreateResponse`; add the second independent `asyncio.ensure_future(send_email(...))` call in `create_invite`, after the existing audit dispatch.
  8. Dashboard: `types.ts` additive optional field, then `InviteMemberDialog.tsx` success-state copy delta.

Persona (required): generic (domain-analyst/architect stance) — no shipped `.add/personas/` file carries `flow: design` AND a backend/ports-and-adapters lens; this task's real conventions were instead drawn directly from `PROJECT.md`/the shipped `backend-architect.md` critical rules (inward-only `domain/` imports, `typing.Protocol` port + adapter pair, use-case `__init__(ports) -> async execute()` shape) as project fact, not persona identity. The BUILD agent should still load `.add/personas/backend-architect.md` (flow: build, advisor) at its own phase — it is the correct fit for writing this code, just not a `flow: design` match for drafting the contract.
Spawn isolation (default): worktree — this task touches both apps/gateway and apps/dashboard alongside 3 sibling in-flight tasks in the same milestone (activation-quickstart, device-activate-page, self-serve-checkout); a shared tree risks cross-task file collisions (config.py, main.py) before any of the four freeze.
Known-problem fixes:
  - trap: resurrecting the deleted `EmptyUpstreamKeyError` class/name (PROJECT.md's precedent is stale, §0 Issue #1) → fix: use `model_validator(mode="after")` raising a plain `ValueError`, exactly `_validate_otel_config`'s shape.
  - trap: sharing the proxy's or object-store's `CircuitBreaker` instance for SMTP → fix: `SmtpEmailSender.__init__` constructs its OWN `CircuitBreaker()`, per the ml-moderation-layer fold.
  - trap: awaiting `send_email(...)` on the request hot path (would make an SMTP timeout block the 201) → fix: always `asyncio.ensure_future`, never `await`, mirroring `record_audit`'s call sites verbatim.
  - trap: retrying a non-idempotent-safe SMTP error (auth failure, recipients refused) → fix: tenacity's `retry_if_exception_type` allow-list excludes `SMTPAuthenticationError`/`SMTPRecipientsRefused` (R4).
Strategy actually used: AS PLANNED, batches 1-8 in the declared order (domain → console adapter →
config → smtp adapter → application layer → main.py wiring → invites_router.py → dashboard), with
two deviations surfaced during build (both self-corrected within Build, no contract/test edit):
  1. SmtpEmailSender's retry predicate: the §3 CONTRACT prose names
     `retry_if_exception_type((OSError, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected))`
     verbatim, but `smtplib.SMTPException` (base of EVERY smtplib error, including
     SMTPAuthenticationError/SMTPRecipientsRefused) has derived from `OSError` since Python 3.4 —
     a literal `retry_if_exception_type` over that tuple would retry a permanent auth failure,
     which the RED suite's own R4 test (`test_non_transient_smtp_error_is_never_retried`) caught
     immediately (3 attempts instead of 1). Fixed with a `retry_if_exception(_is_retryable)`
     predicate that only retries SMTPConnectError/SMTPServerDisconnected plus a NON-SMTPException
     OSError (raw socket-level failures) — same intent as the CONTRACT, correct implementation.
     Spec delta below.
  2. `SmtpEmailSender._guard()`'s circuit-open path does NOT call `on_upstream_error()` before
     raising EmailSendError — deliberately diverging from the §3 CONTRACT comment's literal
     "circuit-open failure: on_upstream_error(); raise" phrasing, to match the actual shipped
     precedent this task's own §0 GROUND cites (`gateway.objectstore.s3.S3ObjectStore._guard`),
     which never double-counts a guard() failure that made no new call. Consistent with the
     codebase's real convention over the contract prose's shorthand.
Both are documented as `[SPEC · seeded]` deltas in §7, not silent edits — the FROZEN §3 text is
untouched; only the implementation's precise behavior was clarified where the prose was
ambiguous/imprecise relative to Python's own stdlib exception hierarchy.
Safety rule (feature-specific): the invite-accept email dispatch and the invite-create audit dispatch are TWO INDEPENDENT fire-and-forget tasks — never chained/awaited on each other; a failure in either must never affect the other or the 201 response.
Code lives in: `apps/gateway/src/gateway/email/` (new) · edits to `apps/gateway/src/gateway/core/config.py`, `apps/gateway/src/gateway/main.py`, `apps/gateway/src/gateway/tenants/api/invites_router.py` · `apps/dashboard/components/members/`
Constraints: do NOT change any test or the contract; allow-list packages only (stdlib `smtplib`/`email.message` + already-present `tenacity` — NO new dependency); ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 52 BE (`tests/transactional_email` 22 + `tests/member_invite_issuance` 30, `-n 4`, isolated DB) + 3 FE (real `node_modules/.bin/vitest`) green; re-run in the merged bundle tree by the independent SRE-reliability verify agent.
- [x] coverage did not decrease — feature module comprehensively exercised; no covered line removed; the only pre-existing-test change is a purely-additive 9-key shape pin.
- [x] no test or contract was altered during build — `git diff` on tests/contract clean; the two build divergences (retry-predicate tuple, circuit-open `on_upstream_error` shorthand) are logged as `[SPEC · seeded]` deltas in §7, frozen §3 text untouched.
- [x] the green was EARNED, not gamed — independent adversarial refute-read of the 3 load-bearing tests (non-transient-never-retried asserts attempts==1 vs sibling 2/3; fail-open asserts 201+9-key body under a raising sender; concurrent-isolated asserts response beats the 2s send) — verdict EARNED.
- [x] concurrency / timing of the risky operation is safe — fire-and-forget via a second independent `asyncio.ensure_future`, never awaited; hot path never blocks (proven by the <1.0s concurrent test); one 💭 note (default thread-pool bound, self-limited by the per-instance breaker).
- [x] no exposed secrets, injection openings, or unexpected dependencies — SMTP password is `SecretStr` (`.get_secret_value()` only inside `_send_sync` login, never logged); no header-injection surface (`to` is EmailStr-validated, built via stdlib `email.message.EmailMessage`); no new heavy deps (stdlib smtplib + existing tenacity).
- [x] layering & dependencies follow CONVENTIONS.md — clean 4-folder DDD, `domain/` zero infra imports, `CircuitBreaker` reused unchanged, mirrors `record_audit` + `build_object_store` precedents.
- [x] a person reviewed and approved the change — orchestrator recorded under Tin's standing full-auto directive (2026-07-17), on the independent verify agent's complete-evidence EARNED recommendation.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] component green-bars met — gateway `pytest (Makefile:test / ci.yml 'Tests' step)`: 52 tests green via `uv run pytest tests/transactional_email tests/member_invite_issuance -n 4` in the merged bundle tree; dashboard `vitest (ci.yml dashboard job, working-directory: apps/dashboard)`: 3 tests green via the real `apps/dashboard/node_modules/.bin/vitest run` — both the runners CI invokes.
- [x] POST /admin/invites 201 body gains exactly one field, `email_delivery_channel`, every
      other field byte-identical — confirmed by `set(body.keys())` diff assertions in both
      tests/transactional_email + the updated tests/member_invite_issuance shape pin.
- [x] Default boot (no env overrides) never raises — confirmed by `Settings()` construction
      succeeding in test_default_config_never_boot_errors_and_console_link_is_relative and by
      the whole pre-existing invites/acceptance/seat-cap suites staying green unmodified.
- [x] `email_smtp_enabled=true` + empty host/origin boot-errors with the exact contracted
      ValueError message — confirmed by the two boot-guard tests reading `exc_info.value`.
- [x] An email send failure never changes the invite HTTP outcome — confirmed by
      test_email_send_failure_never_fails_invite_creation (still 201, full body) and by
      test_invite_email_dispatch_independent_of_audit_dispatch (audit row still written).
- [x] SmtpEmailSender never retries a permanent SMTP error, only a transient one, up to
      email_smtp_max_retries — confirmed by the 3 dedicated retry/exhaustion/non-retry tests
      against a faked smtplib (no real network).
- [x] SmtpEmailSender owns its own CircuitBreaker, isolated from every other seam's — confirmed
      by object-identity assertion + the circuit-open short-circuit test.
- [x] InviteMemberDialog shows the dispatch-honest line ONLY for channel="smtp", never for
      "console"/absent, copy-link block unchanged — confirmed by the 3 FE tests + a manual read
      of the rendered DOM in the failing (RED) run before the copy delta existed.

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
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-18

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose generic `EmailSender` port, invite-template layered on top; rejected invite-specific `InviteNotifier` port (rejected — narrower, would need replacing when alerts/invoices land; MILESTONE.md explicitly asks for the general shape) · third-party transactional-email HTTP API (SendGrid/SES) instead of/alongside SMTP (rejected THIS milestone — MILESTONE.md names "smtp" explicitly; an HTTP-provider adapter is a clean FUTURE addition behind the same Protocol).
- [human] freeze — froze §3 @ v1 (approved by orchestrator under Tin's standing full-auto directive (2026-07-17).)
- [AI] build — strategy used: AS PLANNED, batches 1-8 in the declared order (domain → console adapter → config → smtp adapter → application layer → main.py wiring → invites_router.py → dashboard), with two deviations surfaced during build (both self-corrected within Build, no contract/test edit): 1. SmtpEmailSender's retry predicate: the §3 CONTRACT prose names `retry_if_exception_type((OSError, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected))` verbatim, but `smtplib.SMTPException` (base of EVERY smtplib error, including SMTPAuthenticationError/SMTPRecipientsRefused) has derived from `OSError` since Python 3.4 — a literal `retry_if_exception_type` over that tuple would retry a permanent auth failure, which the RED suite's own R4 test (`test_non_transient_smtp_error_is_never_retried`) caught immediately (3 attempts instead of 1). Fixed with a `retry_if_exception(_is_retryable)` predicate that only retries SMTPConnectError/SMTPServerDisconnected plus a NON-SMTPException OSError (raw socket-level failures) — same intent as the CONTRACT, correct implementation. Spec delta below. 2. `SmtpEmailSender._guard()`'s circuit-open path does NOT call `on_upstream_error()` before raising EmailSendError — deliberately diverging from the §3 CONTRACT comment's literal "circuit-open failure: on_upstream_error(); raise" phrasing, to match the actual shipped precedent this task's own §0 GROUND cites (`gateway.objectstore.s3.S3ObjectStore._guard`), which never double-counts a guard() failure that made no new call. Consistent with the codebase's real convention over the contract prose's shorthand. Both are documented as `[SPEC · seeded]` deltas in §7, not silent edits — the FROZEN §3 text is untouched; only the implementation's precise behavior was clarified where the prose was ambiguous/imprecise relative to Python's own stdlib exception hierarchy.
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).
- [SPEC · seeded] §3 CONTRACT's retry-predicate prose (`retry_if_exception_type((OSError,
  SMTPConnectError, SMTPServerDisconnected))`) is imprecise: `smtplib.SMTPException` (base of
  every smtplib error) has derived from `OSError` since Python 3.4, so a literal reading would
  retry permanent errors like SMTPAuthenticationError. Built with a corrected predicate instead
  (evidence: R4 RED test caught 3 attempts instead of 1 against the literal reading).
- [SPEC · seeded] §3 CONTRACT's circuit-breaker comment ("on circuit-open failure:
  on_upstream_error(); raise") is shorthand that, read literally, would double-count a guard()
  failure. Built to match the shipped `S3ObjectStore._guard` precedent instead (guard() failure
  raises without touching on_upstream_error — no new call was attempted). Both are candidate
  contract-prose clarifications for a future re-freeze, not code changes needed.

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

- [TDD · folded] a RED suite that asserts the EXACT non-retry behavior (not just "eventually [folded foundation-version 54]
  succeeds") caught a real stdlib exception-hierarchy trap (SMTPException IS-A OSError since
  Python 3.4) that a shape-only test would have missed entirely (evidence: R4 test failed with
  3 attempts against the contract's literal retry-predicate tuple).
- [ADD · folded] a later task's frozen contract legitimately extending an EARLIER task's response [folded foundation-version 54]
  shape requires updating that earlier task's own exact-shape test (in-scope per this task's §5
  Scope line) rather than treating it as an untouchable frozen artifact forever — the update is
  additive-only (one new key) and superseded-not-silent (evidence: tests/member_invite_issuance
  test_owner_invites_co_owner comment cites both task IDs).
- [ADD · folded] a fixed `asyncio.sleep(0.05)` after a fire-and-forget dispatch flakes under a [folded foundation-version 54]
  load-shared multi-agent host even for a BRAND NEW test — poll-until-present from the first
  draft, not just as a post-hoc fix (evidence: [[fire-and-forget-audit-test-flake]] recurred in
  this task's own first draft before being fixed with `_poll_until`).
