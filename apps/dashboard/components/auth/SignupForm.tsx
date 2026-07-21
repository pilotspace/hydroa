"use client";

/**
 * SignupForm — tenant signup form
 *
 * Behavior (per frozen contract §3 v2, extended by activation-quickstart §3 v1
 * — the prior 5 behaviors below stay byte-identical; account_type is additive):
 *   1. Client-side Zod validation before any fetch
 *   2. POST /api/auth/signup BFF endpoint with credentials:"include"
 *   3. 201 → router.push("/app/keys"); no localStorage write
 *   4. 409 → inline email field error "An account with this email already exists"
 *   5. Other errors → surface problem+json title
 *   6. account_type ("personal" | "business", "personal" pre-selected) always
 *      sent in the POST body (activation-quickstart M1) — personal is Tin's
 *      locked pricing-model signup default (2026-07-16).
 *
 * signup-refusal-router TASK.md §3 (FROZEN @ v1) — M1-M3, M8-M10 additive:
 *   7. A persistent [data-slot="signup-alt-routes"] panel ("Already have access
 *      another way?") renders UNCONDITIONALLY, before the password field —
 *      route (a) a static "Sign in with SSO instead" link to /login (carrying
 *      ?domain=<typed email's domain> when present), route (b) static
 *      "Have an invite link?" copy, route (c) a request-access mini-form
 *      (POST /api/auth/access-requests). None of the three routes' visibility,
 *      copy, or behavior varies by server state (R-sec-1 anti-enumeration).
 *   8. On 403 ERR_SIGNUP_INVITE_ONLY specifically (read via `problem.code`,
 *      mirroring JoinByDomainForm's bffCode precedent — never bare `status`),
 *      the old dead-end globalError text is retired (never set) — the
 *      always-present panel takes its place. Field values are preserved
 *      (never cleared). Any OTHER 403/5xx keeps the generic globalError path,
 *      byte-unchanged.
 */

import { useState, useEffect, FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { z } from "zod";
import { BffError } from "@/lib/bff-client";
import { Button, Card, CardContent, Input } from "@/components/ui";
import { classifyEmailDomain } from "@/lib/email-domain-routing";

const SignupSchema = z.object({
  tenant_name: z.string().min(1, "Tenant name is required").max(120, "Tenant name must be at most 120 characters"),
  email: z.string().email("Invalid email address"),
  password: z.string().min(10, "Must be at least 10 characters"),
  account_type: z.enum(["personal", "business"]),
});

type AccountType = "personal" | "business";

type FieldErrors = Partial<Record<"tenant_name" | "email" | "password", string>>;

/**
 * signup-refusal-router M2: text after the LAST "@" in a typed email, trimmed —
 * mirrors LoginForm.resolveSsoDomain's own derivation exactly. Empty when no
 * "@" is present (nothing typed, or a bare local-part) so the SSO link falls
 * back to a plain /login with no domain param.
 */
function deriveSsoDomain(rawEmail: string): string {
  const value = rawEmail.trim();
  if (!value.includes("@")) return "";
  return value.slice(value.lastIndexOf("@") + 1).trim();
}

/**
 * domain-aware-auth-routing §3 (FROZEN @ v1) — the three static lead-in strings.
 * "unknown" deliberately has NO lead-in: it renders today's shipped neutral
 * panel, byte-identical (M5, the SAFE DEFAULT).
 */
const PUBLIC_LEAD_IN = "Looks like a personal address — you can create your own workspace.";
const CORPORATE_LEAD_IN = "If your team already uses Hydroa, here's how to get in.";

const REQUEST_ACCESS_CALM_CONFIRMATION = "We'll pass this along.";
const REQUEST_ACCESS_RATE_LIMITED_MESSAGE =
  "You're going a little fast — try again in a moment.";
const REQUEST_ACCESS_INVALID_EMAIL_MESSAGE = "Enter a valid email";
const REQUEST_ACCESS_FALLBACK_MESSAGE = "Something went wrong. Please try again.";

export function SignupForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [tenantName, setTenantName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [accountType, setAccountType] = useState<AccountType>("personal");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  // domain-auto-assign-login M5: true when the BFF signals the signup JOINED an
  // existing verified-domain tenant instead of creating a new workspace.
  const [joinedExistingTenant, setJoinedExistingTenant] = useState(false);

  // signup-refusal-router M4: the request-access mini-form's OWN email value —
  // a separate input from the main signup email field (disambiguates the two
  // accessible names, TASK.md §4 cross-task-drift note), pre-filled from it via
  // the one-way sync effect below.
  const [requestEmail, setRequestEmail] = useState("");
  const [requestFieldError, setRequestFieldError] = useState<string | null>(null);
  const [requestStatusMessage, setRequestStatusMessage] = useState<string | null>(null);
  const [isRequestingAccess, setIsRequestingAccess] = useState(false);

  // M4 "pre-filled from the signup email field's current value" — a one-shot-per-
  // change sync from the main email field. The visitor can still edit the mini-
  // form's own input directly; it just re-syncs the next time the main field
  // itself changes (matching the contract's "pre-filled", not "locked").
  useEffect(() => {
    // One-way sync from an in-component field (not an external system in the
    // usual sense) — mirrors LoginForm's own suppressed pattern for its
    // one-shot seed effects; the alternative (deriving requestEmail inline on
    // every render) would make the mini-form's own independent edits
    // impossible to preserve between renders.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRequestEmail(email);
  }, [email]);

  // homepage-cta-intent-split TASK.md §3 (FROZEN @ v1) M4: a ONE-SHOT
  // `?account_type=business` seed, mirroring LoginForm.tsx:87-119's own
  // shipped `?domain=` seed exactly — browser-only read, avoids a hydration
  // mismatch, [] deps so it NEVER re-fires after mount (a later manual radio
  // click always wins, M4/edge). Any other value (absent, malformed,
  // unrecognized, e.g. "?account_type=enterprise" or "?account_type=") is a
  // no-op — the existing "personal" default stands untouched (R1).
  useEffect(() => {
    const accountTypeParam = searchParams.get("account_type");
    if (accountTypeParam === "business") {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setAccountType("business");
    }
    // any other value: no-op; default "personal" stands
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // unified-signin-entry TASK.md §3 (FROZEN @ v1) M13: a ONE-SHOT `?email=`
  // seed, mirroring the `?account_type=business` seed effect above exactly —
  // browser-only read, [] deps so it NEVER re-fires after mount (a later
  // manual edit always wins), absent/empty value is a strict no-op leaving
  // today's behavior byte-identical (R-g). Carries the email a visitor
  // already typed at /login's "Create a workspace" link.
  useEffect(() => {
    const emailParam = searchParams.get("email");
    if (emailParam) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setEmail(emailParam);
    }
    // any other value (absent/empty): no-op; today's empty default stands
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const ssoDomain = deriveSsoDomain(email);
  const ssoHref = ssoDomain ? `/login?domain=${encodeURIComponent(ssoDomain)}` : "/login";

  // domain-aware-auth-routing M1/M10/M11: a DERIVED value recomputed on every
  // render from the typed string alone — not state, not a useEffect. This is a
  // security choice, not a style one: a derived value cannot desync from the
  // input, and there is no async seam here where a probe could later be added.
  // classifyEmailDomain is a pure, zero-IO function of (email, a static list),
  // so the panel below is a pure function of what the visitor typed. Server
  // state is NOT an input — which is exactly M11, held BY CONSTRUCTION rather
  // than by a downstream check. Never replace this with a lookup.
  const domainClass = classifyEmailDomain(email);
  const leadIn =
    domainClass === "public"
      ? PUBLIC_LEAD_IN
      : domainClass === "corporate"
        ? CORPORATE_LEAD_IN
        : null;

  async function handleRequestAccess() {
    setRequestFieldError(null);
    setRequestStatusMessage(null);
    setIsRequestingAccess(true);
    try {
      const res = await fetch("/api/auth/access-requests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: requestEmail }),
      });

      if (res.ok) {
        // Calm, UNIFORM confirmation (R3/R-sec-1) — never names or implies
        // account/domain state, and never promises a human will see it (§1 ⚠:
        // store-only for v1, no owner-notification path exists yet).
        setRequestStatusMessage(REQUEST_ACCESS_CALM_CONFIRMATION);
        return;
      }
      if (res.status === 422) {
        setRequestFieldError(REQUEST_ACCESS_INVALID_EMAIL_MESSAGE);
        return;
      }
      if (res.status === 429) {
        setRequestStatusMessage(REQUEST_ACCESS_RATE_LIMITED_MESSAGE);
        return;
      }
      // Any other status: a calm, uniform fallback — never surfaces upstream
      // detail (would itself risk becoming a distinguishing signal).
      setRequestStatusMessage(REQUEST_ACCESS_FALLBACK_MESSAGE);
    } catch {
      setRequestStatusMessage(REQUEST_ACCESS_FALLBACK_MESSAGE);
    } finally {
      setIsRequestingAccess(false);
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFieldErrors({});
    setGlobalError(null);

    // Client-side validation
    const result = SignupSchema.safeParse({
      tenant_name: tenantName,
      email,
      password,
      account_type: accountType,
    });
    if (!result.success) {
      const errors: FieldErrors = {};
      for (const issue of result.error.issues) {
        const field = issue.path[0] as keyof FieldErrors;
        if (!errors[field]) errors[field] = issue.message;
      }
      setFieldErrors(errors);
      return;
    }

    setIsSubmitting(true);
    try {
      const res = await fetch("/api/auth/signup", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tenant_name: tenantName,
          email,
          password,
          account_type: accountType,
        }),
      });

      if (!res.ok) {
        // signup-refusal-router M9 (R-drift fix): `code` DOES ride the wire
        // (app/api/auth/signup/route.ts forwards the gateway's problem+json
        // body verbatim) — this hand-rolled BffError construction previously
        // dropped it, leaving handleSubmit unable to distinguish
        // ERR_SIGNUP_INVITE_ONLY from any other 403/5xx. Mirrors
        // JoinByDomainForm's bffCode() precedent: read `code` off the parsed
        // body, carry it onto the thrown BffError's `problem`.
        let problem: { title?: string; status?: number; code?: string };
        try {
          problem = await res.json() as { title?: string; status?: number; code?: string };
        } catch {
          problem = { title: "An error occurred", status: res.status };
        }
        throw new BffError(res.status, {
          title: problem.title ?? "An error occurred",
          status: res.status,
          code: problem.code,
        });
      }

      // domain-auto-assign-login M5: a joined_existing_tenant: true response
      // renders a differentiated "joined" outcome instead of the generic
      // created-path push. Design for failure: an absent/unparseable field
      // defaults to false, keeping the created path byte-identical (frozen
      // signup-account-type pin: unconditional router.push("/app/keys")).
      let joined = false;
      try {
        const data = (await res.json()) as { joined_existing_tenant?: unknown };
        joined = data.joined_existing_tenant === true;
      } catch {
        joined = false;
      }
      if (joined) {
        setJoinedExistingTenant(true);
        return;
      }

      // No localStorage write — cookie is set server-side by the BFF
      router.push("/app/keys");
    } catch (err) {
      if (err instanceof BffError) {
        if (err.status === 409) {
          setFieldErrors({ email: "An account with this email already exists" });
        } else if (err.problem.code === "ERR_SIGNUP_INVITE_ONLY") {
          // signup-refusal-router M8/M9/M10: the old dead-end globalError text is
          // RETIRED for this specific code — never set. The [data-slot=
          // "signup-alt-routes"] panel below is already unconditionally rendered
          // (M1), so it silently "takes its place" without any extra state. The
          // S1 gate itself, its DB-IO ordering, and the ErrorSpec are untouched —
          // this only changes what the CLIENT does with an already-fired 403.
        } else {
          // Any OTHER 403/5xx: the generic globalError path stays byte-unchanged.
          setGlobalError(err.problem.title ?? "An error occurred");
        }
      } else {
        setGlobalError("An unexpected error occurred");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  // domain-auto-assign-login M5: minimal differentiated joined outcome — the
  // polished UI is a later task's job.
  if (joinedExistingTenant) {
    return (
      <Card data-slot="auth-card">
        <CardContent className="flex flex-col gap-4 p-6">
          <p role="status" aria-live="polite" className="text-sm text-foreground">
            You joined your team&apos;s existing workspace.
          </p>
          <Button type="button" className="w-full" onClick={() => router.push("/app/keys")}>
            Continue
          </Button>
        </CardContent>
      </Card>
    );
  }

  // domain-aware-auth-routing M6/M9/R5: the three FROZEN routes, each defined
  // ONCE and reordered by moving the whole subtree — never by rewriting one.
  // Their copy, hrefs, and submit behavior belong to signup-refusal-router §3
  // v1 and are byte-identical to what shipped. All three are rendered in EVERY
  // class: classification changes ORDER and EMPHASIS only, never PRESENCE
  // (hiding one would contradict the frozen unconditional-render property).
  const ssoRoute = (
    <a
      href={ssoHref}
      className="text-sm font-medium text-primary underline-offset-4 hover:underline"
    >
      Sign in with SSO instead
    </a>
  );

  const inviteRoute = (
    <p className="text-sm text-muted-foreground">
      Have an invite link? Open it in this browser to join your team.
    </p>
  );

  const requestAccessRoute = (
    <div className="flex flex-col gap-1.5">
      <label htmlFor="access_request_email" className="text-sm font-medium text-foreground">
        Request access
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          id="access_request_email"
          type="email"
          value={requestEmail}
          onChange={(e) => setRequestEmail(e.target.value)}
          autoComplete="email"
        />
        <Button
          type="button"
          variant="outline"
          disabled={isRequestingAccess}
          onClick={() => void handleRequestAccess()}
        >
          {isRequestingAccess ? "Requesting…" : "Request access"}
        </Button>
      </div>
      {requestFieldError && (
        <p role="alert" aria-live="polite" className="text-sm text-destructive">
          {requestFieldError}
        </p>
      )}
      {requestStatusMessage && (
        <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
          {requestStatusMessage}
        </p>
      )}
    </div>
  );

  return (
    <form onSubmit={handleSubmit} noValidate aria-label="Sign up">
      <Card data-slot="auth-card">
        <CardContent className="flex flex-col gap-4 p-6">
          {/* signup-refusal-router TASK.md §3 (FROZEN @ v1) M1: renders
              UNCONDITIONALLY — first in the form, well before the password
              field — for every visitor, never gated on any error state. Three
              static, always-identical routes (R-sec-1 anti-enumeration:
              nothing here varies by whether a typed email/domain is already a
              customer). Placed FIRST (not merely "before password") so the
              routing panel is the very first thing a keyboard/screen-reader
              user reaches, ahead of the account-type/tenant-name/email fields
              that lead nowhere for a visitor without an invite. */}
          {/* domain-aware-auth-routing TASK.md §3 (FROZEN @ v1): the panel gains
              data-domain-class, a classification-derived lead-in line, and a
              per-class ORDER — and ONLY those. `domainClass` is a pure function
              of the typed email and a compile-time constant list (M11): no
              request is issued to compute or refresh it, at any keystroke, for
              any domain. Two domains in the same shape class render
              byte-identically whether or not either is a Hydroa customer. */}
          <div
            data-slot="signup-alt-routes"
            data-domain-class={domainClass}
            className="flex flex-col gap-4 rounded-md border border-border p-4"
          >
            <p className="text-sm font-medium text-foreground">
              Already have access another way?
            </p>

            {leadIn && <p className="text-sm text-muted-foreground">{leadIn}</p>}

            {domainClass === "public" ? (
              // M7: a personal address leads with self-serve signup; SSO —
              // meaningless on a public provider — is de-emphasized to last.
              // Still PRESENT, never hidden (M6/R5).
              <>
                {inviteRoute}
                {requestAccessRoute}
                {ssoRoute}
              </>
            ) : (
              // M8 (corporate) and M5 (unknown, the SAFE DEFAULT) share today's
              // shipped order, byte-identical: SSO -> invite link -> request access.
              <>
                {ssoRoute}
                {inviteRoute}
                {requestAccessRoute}
              </>
            )}
          </div>

          <fieldset className="flex flex-col gap-1.5">
            <legend className="text-sm font-medium text-foreground">Account type</legend>
            <div className="flex gap-4">
              <label
                htmlFor="account_type_personal"
                className="flex min-h-11 items-center gap-2 text-sm text-foreground"
              >
                <input
                  id="account_type_personal"
                  type="radio"
                  name="account_type"
                  value="personal"
                  checked={accountType === "personal"}
                  onChange={() => setAccountType("personal")}
                  className="size-4 accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                />
                Personal
              </label>
              <label
                htmlFor="account_type_business"
                className="flex min-h-11 items-center gap-2 text-sm text-foreground"
              >
                <input
                  id="account_type_business"
                  type="radio"
                  name="account_type"
                  value="business"
                  checked={accountType === "business"}
                  onChange={() => setAccountType("business")}
                  className="size-4 accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                />
                Business
              </label>
            </div>
          </fieldset>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="tenant_name" className="text-sm font-medium text-foreground">
              Tenant Name
            </label>
            <Input
              id="tenant_name"
              type="text"
              value={tenantName}
              onChange={(e) => setTenantName(e.target.value)}
              autoComplete="organization"
            />
            {fieldErrors.tenant_name && (
              <p role="alert" aria-live="polite" className="text-sm text-destructive">
                {fieldErrors.tenant_name}
              </p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="signup_email" className="text-sm font-medium text-foreground">
              Email
            </label>
            <Input
              id="signup_email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
            {fieldErrors.email && (
              <p role="alert" aria-live="polite" className="text-sm text-destructive">
                {fieldErrors.email}
              </p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="signup_password" className="text-sm font-medium text-foreground">
              Password
            </label>
            <Input
              id="signup_password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
            />
            {fieldErrors.password && (
              <p role="alert" aria-live="polite" className="text-sm text-destructive">
                {fieldErrors.password}
              </p>
            )}
          </div>

          {globalError && (
            <p role="alert" aria-live="polite" className="text-sm text-destructive">
              {globalError}
            </p>
          )}

          <Button type="submit" disabled={isSubmitting} className="w-full">
            {isSubmitting ? "Signing up…" : "Sign up"}
          </Button>
        </CardContent>
      </Card>
    </form>
  );
}
