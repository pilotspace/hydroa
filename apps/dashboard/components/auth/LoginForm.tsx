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

import { useState, useEffect, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { z } from "zod";
import { BffError } from "@/lib/bff-client";
import { Button, Card, CardContent, Input } from "@/components/ui";

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
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [ssoDomain, setSsoDomain] = useState("");
  const [ssoError, setSsoError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Seed the SSO field with the last-used domain (non-secret UI preference).
  // Effect (not a useState initializer) so it never runs during SSR — localStorage
  // is browser-only, and this avoids a hydration mismatch.
  useEffect(() => {
    // One-shot sync from an external store (localStorage) — the SSR-safe pattern for
    // a browser-only seed; not a cascading render. (rule is over-strict here.)
    const saved = readSsoDomain();
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (saved) setSsoDomain(saved);
  }, []);

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

          {globalError && (
            <p role="alert" aria-live="polite" className="text-sm text-destructive">
              {globalError}
            </p>
          )}

          <Button type="submit" disabled={isSubmitting} className="w-full">
            {isSubmitting ? "Signing in…" : "Log in"}
          </Button>

          {/* SSO login — an optional work-email/domain field drives the relay's
              ?domain= so a tenant with per-tenant OIDC configured can start SSO
              from here. Empty field → env-level SSO (no ?domain=). The click does
              a full-page NAVIGATION (window.location.assign) so the browser follows
              the relay's 302 chain to the external IdP — a fetch could not. */}
          <div className="flex flex-col gap-1.5">
            <label htmlFor="sso_domain" className="text-sm font-medium text-foreground">
              Work email or domain
            </label>
            <Input
              id="sso_domain"
              type="text"
              value={ssoDomain}
              onChange={(e) => setSsoDomain(e.target.value)}
              autoComplete="email"
              placeholder="you@company.com"
              aria-describedby={ssoError ? "sso_domain_error" : undefined}
            />
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

          <Button
            type="button"
            variant="outline"
            className="w-full"
            onClick={handleSso}
          >
            Sign in with SSO
          </Button>

          {/* domain-auto-assign-login M6: SAML sibling of the OIDC button above —
              same domain field, full-page navigation to the SAML login relay. */}
          <Button
            type="button"
            variant="outline"
            className="w-full"
            onClick={handleSamlSso}
          >
            Sign in with SAML
          </Button>
        </CardContent>
      </Card>
    </form>
  );
}
