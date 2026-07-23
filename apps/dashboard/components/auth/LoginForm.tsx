"use client";

/**
 * LoginForm — tenant login form
 *
 * Behavior (per frozen contract §3 v2):
 *   1. Client-side Zod validation before fetch
 *   2. POST /api/auth/login BFF endpoint with credentials:"include"
 *   3. 200 → router.push("/app/keys"); no localStorage write
 *   4. 401/error → inline error with problem+json title, no navigation
 */

import { useState, useEffect, FormEvent, ChangeEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { z } from "zod";
import { BffError } from "@/lib/bff-client";
import { Button, Card, CardContent, Input } from "@/components/ui";
import {
  classifyEmailDomain,
  type EmailDomainClass,
} from "@/lib/email-domain-routing";

const LoginSchema = z.object({
  email: z.string().email("Invalid email address"),
  password: z.string().min(1, "Password is required"),
});

type FieldErrors = Partial<Record<"email" | "password", string>>;

const OIDC_LOGIN_PATH = "/api/auth/oidc/login";
/** domain-auto-assign-login M6: the NET-NEW SAML login-init relay. */
const SAML_LOGIN_PATH = "/auth/saml/login";
/** Non-secret UI preference: the last SSO domain a user signed in with. */
const SSO_DOMAIN_KEY = "sso_domain";
/** Bound the pre-flight so a slow/hung gateway never blocks a real login. */
const SSO_PREFLIGHT_TIMEOUT_MS = 5000;
const SSO_NOT_CONFIGURED_MSG =
  "That domain isn’t set up for single sign-on. Check the spelling or contact your administrator.";

/**
 * unified-signin-entry §3 (FROZEN @ v1) M4 — the three static lead-in
 * strings, mirroring SignupForm's own PUBLIC_LEAD_IN/CORPORATE_LEAD_IN
 * constants. "unknown" deliberately has NO lead-in: it renders today's
 * shipped neutral surface, byte-identical (M7, the SAFE DEFAULT).
 */
const PUBLIC_ENTRY_LEAD_IN =
  "Looks like a personal address — sign in, or create your own workspace.";
const CORPORATE_ENTRY_LEAD_IN =
  "If your team already uses Hydroa, sign in with your company account.";
const LOGIN_ENTRY_LEAD_IN_ID = "login_entry_lead_in";

/**
 * SSO-domain preference accessors — defensive on purpose. localStorage is absent
 * during SSR and may throw (private mode, disabled storage, partial test envs),
 * so persistence DEGRADES silently; it is a convenience, never a login blocker.
 */
function readSsoDomain(): string | null {
  try {
    return typeof localStorage !== "undefined" ? localStorage.getItem(SSO_DOMAIN_KEY) : null;
  } catch {
    return null;
  }
}
function persistSsoDomain(domain: string): void {
  try {
    if (typeof localStorage !== "undefined") localStorage.setItem(SSO_DOMAIN_KEY, domain);
  } catch {
    // non-fatal — never block navigation on a storage failure
  }
}

/**
 * Resolve an SSO domain from raw input: a full email yields the part after the
 * last "@"; a bare domain is used as-is. Trimmed + lowercased.
 */
export function resolveSsoDomain(raw: string): string {
  const value = raw.trim().toLowerCase();
  if (value.includes("@")) return value.slice(value.lastIndexOf("@") + 1).trim();
  return value;
}

/**
 * Lenient SSO-domain validation — the gateway is the authority (it 404s an
 * unconfigured domain). We only require a plausible domain shape: a dot, and no
 * spaces or stray "@". Returns null when valid, else an error message.
 */
export function validateSsoDomain(domain: string): string | null {
  if (!/^[^\s@]+\.[^\s@]+$/.test(domain)) {
    return "Enter a valid work email or domain";
  }
  return null;
}

export interface LoginFormProps {
  /**
   * Validated same-origin post-login destination (device-activate-page M1). The parent
   * page has already run it through loginNextTarget/sanitizeNext, so it is a trusted
   * relative path here; defaults to /app/keys.
   */
  nextPath?: string;
}

export function LoginForm({ nextPath = "/app/keys" }: LoginFormProps = {}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // merge-login-email-field §3 (FROZEN @ v1) M6b — TIN'S EXPLICIT FREEZE
  // DECISION: starts false, flips true (permanently) on the FIRST onChange of
  // the merged Email field, never resets. Gates ONLY entryClass's input, not
  // `email` itself — the seeded value still fills the field and still feeds
  // SSO/SAML on click; only the class-driven lead-in/reorder wait for the
  // visitor's own first keystroke. NOT the retired ssoDomainTouched: that
  // guarded a now-deleted email→ssoDomain COPY BRIDGE; this gates
  // CLASSIFICATION, which nothing gated before the merge. Do not delete this
  // as vestigial (ENTRY_VESTIGIAL_STATE is narrowed at freeze so this exact
  // boolean can never trip it).
  const [hasTyped, setHasTyped] = useState(false);
  const [ssoError, setSsoError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // merge-login-email-field §3 M6 — PRISTINE-ONLY SEED, ONE FIELD (supersedes
  // unified-signin-entry M10 for this surface): the one-shot `?domain=` (else
  // localStorage["sso_domain"]) seed sets `email` ONLY while `email` is still
  // "" at the moment this effect runs, via a FUNCTIONAL update — never a
  // stale-closure overwrite. Effect (not a useState initializer) so neither
  // read runs during SSR — both localStorage and useSearchParams are
  // browser-only reads here, and this avoids a hydration mismatch. Precedence
  // (`?domain=` over localStorage) is preserved by the `??` short-circuit,
  // unchanged from the prior two-branch effect. No `ssoDomainTouched`-style
  // flag exists anywhere in this file: "touched" now means exactly "the field
  // is non-empty", which typing can only ever make true, never reverse.
  useEffect(() => {
    const seed = searchParams.get("domain") ?? readSsoDomain();
    if (seed) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setEmail((prev) => (prev === "" ? seed : prev));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /**
   * merge-login-email-field §3 M1/M2 — the ONE email-field change handler.
   * Every keystroke updates the sole `email` state (fed to handleSubmit,
   * handleSso, handleSamlSso, and — once hasTyped is true — entryClass) and
   * flips hasTyped permanently (M6b).
   */
  function handleEmailChange(e: ChangeEvent<HTMLInputElement>) {
    setEmail(e.target.value);
    setHasTyped(true);
  }

  async function handleSso() {
    setSsoError(null);
    setFieldErrors({}); // M5: a stale password-shape error can never linger
    // under the field the visitor is now using for SSO.
    const raw = email.trim();
    if (raw === "") {
      // Empty field → env-level SSO fallback (no ?domain=), unchanged behavior.
      window.location.assign(OIDC_LOGIN_PATH);
      return;
    }
    const domain = resolveSsoDomain(raw);
    const error = validateSsoDomain(domain);
    if (error) {
      setSsoError(error); // block navigation; the gateway never sees a bad domain
      return;
    }
    const target = `${OIDC_LOGIN_PATH}?domain=${encodeURIComponent(domain)}`;
    // Pre-flight the relay to catch an unconfigured domain (it 404s) BEFORE the
    // full-page nav — otherwise the browser dead-ends on a raw 404 page. With
    // redirect:"manual" a configured 3xx is an opaqueredirect (status 0) or a
    // readable 302 depending on the runtime, so "not configured" == a 4xx.
    try {
      const preflight = await fetch(target, {
        redirect: "manual",
        signal: AbortSignal.timeout(SSO_PREFLIGHT_TIMEOUT_MS),
      });
      if (preflight.status >= 400 && preflight.status < 500) {
        setSsoError(SSO_NOT_CONFIGURED_MSG); // unconfigured domain — no navigation
        return;
      }
      // Verified configured (non-4xx): persist the last-used domain. We persist
      // ONLY on a confirmed-good probe, never on the degrade path below.
      persistSsoDomain(domain);
    } catch {
      // Probe failed (network/timeout) — DEGRADE to the direct full-page nav so a
      // flaky probe never blocks a real login (no persist: the domain is unverified).
    }
    // Full-page navigation so the browser follows the relay's 302 chain to the IdP.
    window.location.assign(target);
  }

  /**
   * domain-auto-assign-login M6: SAML SSO affordance. Same "Work email or
   * domain" field as OIDC, but SAML always requires a resolvable domain (the
   * gateway's /auth/saml/login resolves the tenant config by domain) and no
   * pre-flight probe: a full-page navigation carries the browser through the
   * relay's 302 chain to the IdP; a gateway 4xx surfaces on the relay itself.
   */
  function handleSamlSso() {
    setSsoError(null);
    setFieldErrors({}); // M5: symmetric with handleSso — see above.
    const domain = resolveSsoDomain(email);
    const error = validateSsoDomain(domain);
    if (error) {
      setSsoError(error); // block navigation; the gateway never sees a bad domain
      return;
    }
    // Full-page navigation so the browser follows the relay's 302 to the IdP.
    window.location.assign(`${SAML_LOGIN_PATH}?domain=${encodeURIComponent(domain)}`);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFieldErrors({});
    setGlobalError(null);

    // Client-side validation
    const result = LoginSchema.safeParse({ email, password });
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
      const res = await fetch("/api/auth/login", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });

      if (!res.ok) {
        let problem: { title?: string };
        try {
          problem = await res.json() as { title?: string };
        } catch {
          problem = { title: "An error occurred" };
        }
        throw new BffError(res.status, {
          title: problem.title ?? "An error occurred",
          status: res.status,
        });
      }

      // No localStorage write — cookie is set server-side by the BFF. Return to the
      // validated `next` destination (e.g. /activate?user_code=…) or the default.
      router.push(nextPath);
    } catch (err) {
      if (err instanceof BffError) {
        setGlobalError(err.problem.title ?? "An error occurred");
      } else {
        setGlobalError("An unexpected error occurred");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  // unified-signin-entry M1/M2: a DERIVED value recomputed on every render
  // from the typed email string alone — never state, never a useEffect.
  // classifyEmailDomain is a pure, zero-IO function of (email, a static
  // list), so entryClass (and everything below it) is a pure function of
  // what the visitor typed. Server state is NOT an input (M11). Never
  // replace this with a lookup — mirrors SignupForm's own domainClass.
  const entryClass: EmailDomainClass = classifyEmailDomain(email);
  // merge-login-email-field M6b: what the UI ACTS ON is gated on hasTyped so
  // a SEEDED value (which now shares the same `email` state) cannot
  // pre-classify the form before the visitor's own first keystroke —
  // preserving today's exact "always neutral until you type" on-load
  // behavior. `entryClass` above stays the pure, ungated classification of
  // whatever is currently typed (M11); `visibleClass` is the one every
  // render decision below reads.
  const visibleClass: EmailDomainClass = hasTyped ? entryClass : "unknown";
  const leadInText =
    visibleClass === "corporate"
      ? CORPORATE_ENTRY_LEAD_IN
      : visibleClass === "public"
        ? PUBLIC_ENTRY_LEAD_IN
        : null;
  const leadInId = leadInText ? LOGIN_ENTRY_LEAD_IN_ID : undefined;

  // unified-signin-entry M8/R7: a NEW, ALWAYS-PRESENT route off this page — a
  // plain anchor, never a fetch. email === "" -> "/signup" (no empty param).
  const createWorkspaceHref =
    email === ""
      ? "/signup"
      : `/signup?email=${encodeURIComponent(email)}${
          visibleClass === "corporate" ? "&account_type=business" : ""
        }`;

  // unified-signin-entry M9/R5: the four affordances, each defined ONCE and
  // reordered below by moving the whole subtree — never by rewriting one in
  // place. Their own copy/href/handler are byte-identical to what shipped.
  const passwordRoute = (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <label htmlFor="login_password" className="text-sm font-medium text-foreground">
          Password
        </label>
        <Input
          id="login_password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
        />
        {fieldErrors.password && (
          <p role="alert" aria-live="polite" className="text-sm text-destructive">
            {fieldErrors.password}
          </p>
        )}
      </div>
      <Button type="submit" disabled={isSubmitting} className="w-full">
        {isSubmitting ? "Signing in…" : "Log in"}
      </Button>
    </div>
  );

  const ssoRoute = (
    <div className="flex flex-col gap-4">
      {/* merge-login-email-field §3: the SSO login affordance no longer has
          its own field — it reads the merged Email field above via
          resolveSsoDomain(email). Empty field → env-level SSO (no ?domain=).
          The click does a full-page NAVIGATION (window.location.assign) so
          the browser follows the relay's 302 chain to the external IdP — a
          fetch could not. M8: the error travels with this subtree under
          per-class reordering, beside the button it concerns; it carries no
          id (nothing describes it via aria-describedby anymore). */}
      <Button type="button" variant="outline" className="w-full" onClick={handleSso}>
        Sign in with SSO
      </Button>
      {ssoError && (
        <p role="alert" aria-live="polite" className="text-sm text-destructive">
          {ssoError}
        </p>
      )}
    </div>
  );

  // domain-auto-assign-login M6: SAML sibling of the OIDC route above — same
  // domain field, full-page navigation to the SAML login relay.
  const samlRoute = (
    <Button type="button" variant="outline" className="w-full" onClick={handleSamlSso}>
      Sign in with SAML
    </Button>
  );

  const createWorkspaceRoute = (
    <a
      href={createWorkspaceHref}
      className="text-center text-sm font-medium text-primary underline-offset-4 hover:underline"
    >
      Create a workspace
    </a>
  );

  return (
    <form onSubmit={handleSubmit} noValidate aria-label="Log in">
      <Card data-slot="auth-card">
        <CardContent className="flex flex-col gap-4 p-6">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="login_email" className="text-sm font-medium text-foreground">
              Email
            </label>
            <Input
              id="login_email"
              type="email"
              value={email}
              onChange={handleEmailChange}
              autoComplete="email"
              placeholder="you@company.com"
            />
            {fieldErrors.email && (
              <p role="alert" aria-live="polite" className="text-sm text-destructive">
                {fieldErrors.email}
              </p>
            )}
          </div>

          {globalError && (
            <p role="alert" aria-live="polite" className="text-sm text-destructive">
              {globalError}
            </p>
          )}

          {/* unified-signin-entry §3 (FROZEN @ v1): the region gains
              data-domain-class, a classification-derived lead-in, and a
              per-class ORDER over the four affordances — and ONLY those.
              entryClass is a pure function of the typed email and a
              compile-time constant list (M11): no request is issued to
              compute or refresh it, at any keystroke, for any domain. Two
              domains in the same shape class render byte-identically
              regardless of tenant/claim/user/SSO-config existence. */}
          <div
            data-slot="login-entry-routes"
            data-domain-class={visibleClass}
            aria-describedby={leadInId}
            className="flex flex-col gap-4"
          >
            {leadInText && (
              <p
                id={LOGIN_ENTRY_LEAD_IN_ID}
                aria-live="polite"
                className="text-sm text-muted-foreground"
              >
                {leadInText}
              </p>
            )}

            {visibleClass === "corporate" ? (
              // M5: a corporate work email leads with company sign-in.
              <>
                {ssoRoute}
                {samlRoute}
                {passwordRoute}
                {createWorkspaceRoute}
              </>
            ) : visibleClass === "public" ? (
              // M6: a personal address leads with self-serve signup.
              <>
                {createWorkspaceRoute}
                {passwordRoute}
                {ssoRoute}
                {samlRoute}
              </>
            ) : (
              // M7 (unknown, the SAFE DEFAULT): today's shipped order,
              // byte-identical, with create-workspace appended.
              <>
                {passwordRoute}
                {ssoRoute}
                {samlRoute}
                {createWorkspaceRoute}
              </>
            )}
          </div>
        </CardContent>
      </Card>
    </form>
  );
}
