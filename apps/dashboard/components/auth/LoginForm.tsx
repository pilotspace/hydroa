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
  normalizeEmailDomain,
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
const SSO_DOMAIN_HELP_ID = "sso_domain_help";

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
  const [ssoDomain, setSsoDomain] = useState("");
  // unified-signin-entry M10/R6: PRISTINE-ONLY auto-seed guard. Flips to true,
  // permanently, on EITHER the SSO field's own onChange OR the one-shot
  // ?domain=/localStorage seed effect below assigning a value. Once true the
  // auto-seed (wired on the Email field's onChange) never overwrites it again.
  const [ssoDomainTouched, setSsoDomainTouched] = useState(false);
  const [ssoError, setSsoError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Seed the SSO field: signup-refusal-router TASK.md §3 (FROZEN @ v1) M2 —
  // a ONE-SHOT `?domain=` query-param seed (arriving from the signup routing
  // panel's SSO link), additive to the existing ONE-SHOT localStorage seed
  // below. Effect (not a useState initializer) so neither runs during SSR —
  // both localStorage and useSearchParams are browser-only reads here, and
  // this avoids a hydration mismatch. Precedence (OPEN per TASK.md §4's own
  // flag — not specified by §3): a present `?domain=` wins over a stored
  // localStorage value, since it reflects the visitor's just-made intent;
  // absent param falls back to the existing localStorage seed, unchanged.
  useEffect(() => {
    const domainParam = searchParams.get("domain");
    if (domainParam) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSsoDomain(domainParam);
      // unified-signin-entry M10/R6: a seed-assigned value counts as touched —
      // the auto-seed below must never clobber it.
      setSsoDomainTouched(true);
      return;
    }
    // One-shot sync from an external store (localStorage) — the SSR-safe pattern for
    // a browser-only seed; not a cascading render. (rule is over-strict here.)
    const saved = readSsoDomain();
    if (saved) {
      setSsoDomain(saved);
      setSsoDomainTouched(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /**
   * unified-signin-entry M10/R6: the ONE Email-field change handler. While the
   * SSO domain field is still pristine (never touched by the visitor nor by
   * the seed effect above), every keystroke also auto-fills ssoDomain with
   * normalizeEmailDomain(value) — the "type your email once" collapse. Once
   * ssoDomainTouched flips true (either path), this branch is permanently
   * inert: the auto-seed may NEVER overwrite a touched value.
   */
  function handleEmailChange(e: ChangeEvent<HTMLInputElement>) {
    const value = e.target.value;
    setEmail(value);
    if (!ssoDomainTouched) {
      setSsoDomain(normalizeEmailDomain(value));
    }
  }

  async function handleSso() {
    setSsoError(null);
    const raw = ssoDomain.trim();
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
    const domain = resolveSsoDomain(ssoDomain);
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
  const leadInText =
    entryClass === "corporate"
      ? CORPORATE_ENTRY_LEAD_IN
      : entryClass === "public"
        ? PUBLIC_ENTRY_LEAD_IN
        : null;
  const leadInId = leadInText ? LOGIN_ENTRY_LEAD_IN_ID : undefined;

  // unified-signin-entry M8/R7: a NEW, ALWAYS-PRESENT route off this page — a
  // plain anchor, never a fetch. email === "" -> "/signup" (no empty param).
  const createWorkspaceHref =
    email === ""
      ? "/signup"
      : `/signup?email=${encodeURIComponent(email)}${
          entryClass === "corporate" ? "&account_type=business" : ""
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
      {/* SSO login — an optional work-email/domain field drives the relay's
          ?domain= so a tenant with per-tenant OIDC configured can start SSO
          from here. Empty field → env-level SSO (no ?domain=). The click does
          a full-page NAVIGATION (window.location.assign) so the browser follows
          the relay's 302 chain to the external IdP — a fetch could not.
          unified-signin-entry M10/R6: onChange also flips ssoDomainTouched so
          the Email-field auto-seed never clobbers a visitor-typed value. */}
      <div className="flex flex-col gap-1.5">
        <label htmlFor="sso_domain" className="text-sm font-medium text-foreground">
          Work email or domain
        </label>
        <Input
          id="sso_domain"
          type="text"
          value={ssoDomain}
          onChange={(e) => {
            setSsoDomain(e.target.value);
            setSsoDomainTouched(true);
          }}
          autoComplete="email"
          placeholder="you@company.com"
          aria-describedby={
            ssoError ? `sso_domain_error ${SSO_DOMAIN_HELP_ID}` : SSO_DOMAIN_HELP_ID
          }
        />
        <p id={SSO_DOMAIN_HELP_ID} className="text-xs text-muted-foreground">
          Optional — auto-filled from your email address; you can also enter it yourself.
        </p>
        {ssoError && (
          <p
            id="sso_domain_error"
            role="alert"
            aria-live="polite"
            className="text-sm text-destructive"
          >
            {ssoError}
          </p>
        )}
      </div>

      <Button type="button" variant="outline" className="w-full" onClick={handleSso}>
        Sign in with SSO
      </Button>
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
            data-domain-class={entryClass}
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

            {entryClass === "corporate" ? (
              // M5: a corporate work email leads with company sign-in.
              <>
                {ssoRoute}
                {samlRoute}
                {passwordRoute}
                {createWorkspaceRoute}
              </>
            ) : entryClass === "public" ? (
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
