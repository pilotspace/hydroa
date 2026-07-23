/**
 * sso-login-button (v31) + sso-login-polish (v37) — TASK.md §4.
 *
 * v31: a "Work email or domain" field drives the "Sign in with SSO" button's
 * ?domain= via a full-page window.location.assign (so the browser follows the
 * relay's 302 chain to the IdP — a fetch alone could not complete that).
 *
 * v37 polish (frozen §3 v1): before navigating, handleSso PRE-FLIGHTS
 * GET /api/auth/oidc/login?domain= ({redirect:"manual"}). A 4xx (unconfigured
 * domain) → an inline ssoError + NO navigation; otherwise persist the domain in
 * localStorage["sso_domain"] and navigate. A fetch error DEGRADES to direct nav.
 * On mount the field is seeded from localStorage["sso_domain"].
 *
 * NOTE on the pre-flight status check: with redirect:"manual" a configured 3xx
 * surfaces as either an opaqueredirect (status 0) or a readable 302 depending on
 * the runtime — so "configured" is anything NOT a 4xx. Tests mock both arms.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { LoginForm } from "@/components/auth/LoginForm";

const OIDC_LOGIN = "/api/auth/oidc/login";
const APP = "http://localhost:3000";

/** Default relay arm: domain IS configured → a 302 toward the IdP. */
function relayConfigured() {
  return http.get(`${APP}${OIDC_LOGIN}`, () =>
    new HttpResponse(null, { status: 302, headers: { location: "https://idp.example/authorize" } }),
  );
}
/** Relay arm: domain is NOT configured → 404 ERR_OIDC_NOT_CONFIGURED. */
function relayNotConfigured() {
  return http.get(`${APP}${OIDC_LOGIN}`, () =>
    HttpResponse.json({ code: "ERR_OIDC_NOT_CONFIGURED" }, { status: 404 }),
  );
}

describe("LoginForm — SSO domain field", () => {
  const user = userEvent.setup();
  // jsdom's window.location.assign is non-configurable, so spyOn fails; redefine
  // window.location wholesale with a mock assign for the duration of each test.
  const assign = vi.fn();
  let originalLocation: Location;

  beforeEach(() => {
    originalLocation = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: {
        ...originalLocation,
        assign,
        href: "http://localhost:3000/login",
        origin: "http://localhost:3000",
      },
    });
    assign.mockClear();
    localStorage.clear();
    // Configured by default so the happy-path nav tests reach window.location.assign.
    server.use(relayConfigured());
  });
  afterEach(() => {
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: originalLocation,
    });
    localStorage.clear();
  });

  // merge-login-email-field §3 (FROZEN @ v1) retarget register: the SSO field
  // and #login_email are now the SAME node — select it by its one accessible
  // name, "Email" (was "Work email or domain").
  async function clickSso(value?: string) {
    if (value) {
      await user.type(screen.getByLabelText(/^email$/i), value);
    }
    await user.click(screen.getByRole("button", { name: /sign in with sso/i }));
  }

  it("test_sso_with_email_sends_domain", async () => {
    render(<LoginForm />);
    await clickSso("alice@acme.com");
    // Nav now happens after the awaited pre-flight resolves.
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`${OIDC_LOGIN}?domain=acme.com`));
  });

  it("test_sso_with_bare_domain", async () => {
    render(<LoginForm />);
    await clickSso("acme.com");
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`${OIDC_LOGIN}?domain=acme.com`));
  });

  it("test_sso_empty_keeps_env_fallback", async () => {
    render(<LoginForm />);
    await clickSso(); // empty field → env fallback, no pre-flight
    await waitFor(() => expect(assign).toHaveBeenCalledWith(OIDC_LOGIN)); // no ?domain=
  });

  it("test_sso_malformed_blocks_navigation", async () => {
    render(<LoginForm />);
    await clickSso("notadomain"); // no dot → invalid, blocked before any fetch
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();
  });

  it("test_password_login_unaffected", async () => {
    // merge-login-email-field §3 retarget register: STRUCTURAL rewrite — the
    // shipped test typed "acme.com" into #sso_domain THEN "ada@acme.io" into
    // #login_email, which is impossible now that there is one field. Re-expressed
    // as a single type into the merged field, submitted as a password login;
    // the guarantee (password submit never triggers SSO navigation) is intact.
    let loginCalled = false;
    server.use(
      http.post("http://localhost:3000/api/auth/login", () => {
        loginCalled = true;
        return HttpResponse.json({ ok: true }, { status: 200 });
      }),
    );
    render(<LoginForm />);

    await user.type(screen.getByLabelText(/^email$/i), "ada@acme.io");
    await user.type(screen.getByLabelText(/password/i), "hunter12345");
    await user.click(screen.getByRole("button", { name: /^log in$/i }));

    await waitFor(() => expect(loginCalled).toBe(true));
    expect(assign).not.toHaveBeenCalled(); // password submit ≠ SSO navigation
  });

  // ── v37 polish ─────────────────────────────────────────────────────────────

  it("test_sso_prefills_last_domain", async () => {
    // Retarget: selector only — the seeded value now pre-fills the SAME field
    // password login also uses (⚠ disclosed at CONTRACT); the prefill guarantee
    // itself is intact.
    localStorage.setItem("sso_domain", "acme.com");
    render(<LoginForm />);
    await waitFor(() =>
      expect(screen.getByLabelText(/^email$/i)).toHaveValue("acme.com"),
    );
  });

  it("test_sso_configured_navigates_and_persists", async () => {
    server.use(relayConfigured());
    render(<LoginForm />);
    await clickSso("acme.com");
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`${OIDC_LOGIN}?domain=acme.com`));
    expect(localStorage.getItem("sso_domain")).toBe("acme.com");
  });

  it("test_sso_unconfigured_shows_message", async () => {
    server.use(relayNotConfigured());
    render(<LoginForm />);
    await clickSso("nope.com");
    // Inline message about an unconfigured domain; no navigation; bad domain not persisted.
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/single sign-on|sign on|administrator/i),
    );
    expect(assign).not.toHaveBeenCalled();
    expect(localStorage.getItem("sso_domain")).not.toBe("nope.com");
  });

  it("test_sso_preflight_error_degrades", async () => {
    // Relay unreachable / network error → degrade to direct navigation (never block a real login).
    server.use(http.get(`${APP}${OIDC_LOGIN}`, () => HttpResponse.error()));
    render(<LoginForm />);
    await clickSso("acme.com");
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`${OIDC_LOGIN}?domain=acme.com`));
    // Degrade navigates but does NOT persist an unverified domain.
    expect(localStorage.getItem("sso_domain")).toBeNull();
  });

  // ── merge-login-email-field (M5, R4) — NEW: the merge's one fresh risk ─────
  // Neither suite could cover this pre-merge (the two fields were visually
  // distinct, so their errors could never collide). Not individually named in
  // §3's per-file retarget register, but required by the FROZEN contract's own
  // ENTRY_STALE_ERROR_CARRIED refusal.
  //
  // NOTE — only ONE direction is tested here. §2 lists a symmetric-looking
  // scenario ("a stale SSO error never survives a successful password
  // login"), but §3 CONTRACT item 4 is explicit and authoritative: "The
  // reverse is NOT required — handleSubmit does not need to clear ssoError —
  // see §1 Assumption 5 for the accepted, out-of-scope asymmetric residue in
  // that direction." Writing a test enforcing the reverse would contradict
  // the frozen contract's own text, so it is deliberately NOT added here —
  // flagged in the build report rather than silently either added or ignored.

  it("test_stale_password_error_never_survives_a_successful_sso_click", async () => {
    // covers: M5, R4 — §2 "A stale password error never survives into a
    // successful SSO click"
    render(<LoginForm />);
    await user.type(screen.getByLabelText(/^email$/i), "not-an-email");
    await user.click(screen.getByRole("button", { name: /^log in$/i }));
    await waitFor(() => expect(screen.getByText("Invalid email address")).toBeInTheDocument());

    await user.clear(screen.getByLabelText(/^email$/i));
    await user.type(screen.getByLabelText(/^email$/i), "acme.com");
    await user.click(screen.getByRole("button", { name: /sign in with sso/i }));

    expect(screen.queryByText("Invalid email address")).not.toBeInTheDocument();
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`${OIDC_LOGIN}?domain=acme.com`));
  });
});
