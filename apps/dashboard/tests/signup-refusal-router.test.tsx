/**
 * tests/signup-refusal-router.test.tsx — RED suite for `signup-refusal-router`
 * TASK.md §3 (FROZEN @ v1), the dashboard half of the contract: M1-M3, M8-M10,
 * the request-access mini-form (M4-M6, R1, R2), and its accessibility bar.
 *
 * Persona: Frontend Engineer (`.add/personas/frontend-engineer.md`) — the
 * dashboard is a trust boundary before it is a UI; every assertion below reads
 * OBSERVABLE behavior (DOM text/role/href/value), never an implementation
 * internal, with one deliberate exception documented inline (the
 * `data-slot="signup-alt-routes"` anchor, which §3 itself names verbatim).
 *
 * RED reason (today, before Build): SignupForm renders NONE of the M1 panel
 * (no "Already have access another way?" text, no SSO link, no invite-link
 * copy, no request-access mini-form) — every panel-scoped query below throws
 * "Unable to find element" / returns null. The 403 test additionally fails
 * because today's handleSubmit special-cases ONLY `err.status === 409`; any
 * other status (including today's 403 ERR_SIGNUP_INVITE_ONLY) falls through to
 * `setGlobalError(err.problem.title)`, rendering the literal dead-end text this
 * task retires — so "the dead-end text is NOT present" fails today (it IS
 * present), for the right reason.
 *
 * KNOWN TENSION (flagged, not silently resolved — see final report
 * open_questions): §2's "An unrelated 403 does NOT trigger the routing panel"
 * scenario reads, taken literally, as if the panel toggles visibility. §1 M1
 * ("renders on /signup UNCONDITIONALLY... not gated on any error state") and
 * §3 ("renders the SAME panel IN PLACE of today's dead-end role="alert" text")
 * are more specific and were frozen after the scenario prose; both agree the
 * panel is ALWAYS present and only the *dead-end/global-error TEXT* is what
 * varies by code. test_403_unrelated_code_keeps_generic_error_path follows
 * §1/§3 (the more specific, later text) rather than §2's shorthand — flagged
 * for human confirmation at BUILD/VERIFY, not silently decided as final.
 */

import { describe, it, expect } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { axe } from "@/test-support/axe";

import { SignupForm } from "@/components/auth/SignupForm";

const APP = "http://localhost:3000";

/**
 * The panel anchor is NOT an invented internal — §3 CONTRACT names it
 * verbatim: "A persistent panel (proposed data-slot="signup-alt-routes")".
 * Scoping through it is the only way to disambiguate the request-access
 * mini-form's own email input from the main signup email input (both
 * accessible names will contain "email" once M4 ships — see
 * mainSignupEmailInput()'s doc comment).
 */
function getPanel(container: HTMLElement): HTMLElement {
  const panel = container.querySelector('[data-slot="signup-alt-routes"]');
  if (!panel) {
    throw new Error(
      'Panel [data-slot="signup-alt-routes"] not found — M1 "Already have access another way?" panel is missing',
    );
  }
  return panel as HTMLElement;
}

/**
 * The MAIN signup email field, disambiguated from the request-access
 * mini-form's own email input (M4) by its existing, pre-task id
 * (SignupForm.tsx: <Input id="signup_email" .../>). A bare
 * `getByLabelText(/email/i)` becomes ambiguous the moment a second
 * email-ish-labelled field exists on the page — exactly the risk this task's
 * Build must navigate WITHOUT touching the frozen `tests/signup.test.tsx`
 * suite, which still calls `getByLabelText(/email/i)` for this same field.
 */
function mainSignupEmailInput(): HTMLInputElement {
  const el = document.getElementById("signup_email");
  if (!el) throw new Error("main signup email input (#signup_email) not found");
  return el as HTMLInputElement;
}

async function fillMainSignup(
  user: ReturnType<typeof userEvent.setup>,
  overrides?: { tenant_name?: string; email?: string; password?: string },
) {
  await user.type(screen.getByLabelText(/tenant name/i), overrides?.tenant_name ?? "Acme");
  await user.type(mainSignupEmailInput(), overrides?.email ?? "sam@acme.com");
  await user.type(screen.getByLabelText(/password/i), overrides?.password ?? "hunter12345");
}

describe("SignupForm — M1: the always-visible routing panel", () => {
  it("test_panel_visible_before_password_unconditional", async () => {
    // covers: M1 — panel present before any field is typed/submitted, positioned
    // before the password field.
    const { container } = render(<SignupForm />);

    const panel = getPanel(container);
    const passwordInput = screen.getByLabelText(/password/i);

    // Panel precedes the password field in DOM order (Node.DOCUMENT_POSITION_FOLLOWING
    // set on the relationship panel -> passwordInput means passwordInput follows panel).
    const position = panel.compareDocumentPosition(passwordInput);
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    // The three named routes are all present inside the panel from first render.
    expect(within(panel).getByText(/already have access another way\?/i)).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: /sign in with sso instead/i })).toBeInTheDocument();
    expect(within(panel).getByText(/have an invite link/i)).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: /request access/i })).toBeInTheDocument();
  });
});

describe("SignupForm — M2: SSO route carries the typed email's domain", () => {
  const user = userEvent.setup();

  it("test_sso_link_carries_typed_email_domain", async () => {
    // covers: M2 — typed "sam@acme.com" -> SSO link href carries ?domain=acme.com
    const { container } = render(<SignupForm />);
    await user.type(mainSignupEmailInput(), "sam@acme.com");

    const panel = getPanel(container);
    const ssoLink = within(panel).getByRole("link", { name: /sign in with sso instead/i });
    expect(ssoLink).toHaveAttribute("href", "/login?domain=acme.com");
  });

  it("test_sso_link_plain_when_no_email_typed", async () => {
    // covers: M2 — nothing typed -> plain /login, no domain param; existing
    // localStorage SSO seed (unchanged) is LoginForm's own concern, not this link's.
    const { container } = render(<SignupForm />);

    const panel = getPanel(container);
    const ssoLink = within(panel).getByRole("link", { name: /sign in with sso instead/i });
    expect(ssoLink).toHaveAttribute("href", "/login");
  });
});

describe("SignupForm — M3: the invite-link route is static copy only", () => {
  it("test_invite_link_copy_no_new_route", async () => {
    // covers: M3 — informational copy only, no new form/route rendered for it.
    const { container } = render(<SignupForm />);
    const panel = getPanel(container);

    expect(
      within(panel).getByText(/have an invite link\?.*open it in this browser.*join your team/i),
    ).toBeInTheDocument();

    // No second <form> and no additional text input is introduced BY route (b) —
    // the only interactive control besides the main form's own fields is the
    // request-access mini-form's single email input + submit button (M4).
    const panelTextboxes = within(panel).getAllByRole("textbox");
    expect(panelTextboxes.length).toBe(1); // exactly the request-access input, not two
  });
});

describe("SignupForm — request-access mini-form (M4, M5, M6, R1, R2)", () => {
  const user = userEvent.setup();

  it("test_request_access_prefilled_from_signup_email", async () => {
    // covers: M4 — the mini-form's email input is pre-filled from the signup
    // email field's CURRENT value at render/typing time.
    const { container } = render(<SignupForm />);
    await user.type(mainSignupEmailInput(), "sam@acme.com");

    const panel = getPanel(container);
    const requestInput = within(panel).getByRole("textbox");
    await waitFor(() => expect(requestInput).toHaveValue("sam@acme.com"));
  });

  it("test_request_access_success_shows_calm_uniform_confirmation", async () => {
    // covers: M4, M5, M6 — POST /api/auth/access-requests {email} -> 202 uniform
    // success -> exactly one calm role="status" aria-live="polite" confirmation
    // that never names or implies account/domain state (R3 boundary — the true
    // uniform-response invariant is the gateway's job; this only asserts the
    // CLIENT never surfaces a distinguishing message).
    let capturedBody: unknown = null;
    server.use(
      http.post(`${APP}/api/auth/access-requests`, async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({ ok: true }, { status: 202 });
      }),
    );

    const { container } = render(<SignupForm />);
    await user.type(mainSignupEmailInput(), "sam@acme.com");
    const panel = getPanel(container);
    const requestInput = within(panel).getByRole("textbox");
    await waitFor(() => expect(requestInput).toHaveValue("sam@acme.com"));
    await user.click(within(panel).getByRole("button", { name: /request access/i }));

    const status = await within(panel).findByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status.textContent?.toLowerCase()).not.toMatch(
      /already|exist|customer|registered|domain is|account/,
    );

    expect(capturedBody).toEqual({ email: "sam@acme.com" });
  });

  it("test_request_access_malformed_email_422_inline_alert", async () => {
    // covers: R1 — 422 -> role="alert" inline "Enter a valid email" under the
    // request-access input (exact copy, §3 has no "e.g." hedge on this one).
    server.use(
      http.post(`${APP}/api/auth/access-requests`, () =>
        HttpResponse.json({ detail: [{ msg: "value is not a valid email address" }] }, { status: 422 }),
      ),
    );

    const { container } = render(<SignupForm />);
    const panel = getPanel(container);
    const requestInput = within(panel).getByRole("textbox");
    await user.type(requestInput, "not-an-email");
    await user.click(within(panel).getByRole("button", { name: /request access/i }));

    const alert = await within(panel).findByRole("alert");
    expect(alert).toHaveTextContent(/enter a valid email/i);
  });

  it("test_request_access_rate_limited_429_status_message", async () => {
    // covers: R2 — 429 -> role="status" "You're going a little fast — try again
    // in a moment." (exact copy, §3 has no "e.g." hedge on this one either).
    server.use(
      http.post(`${APP}/api/auth/access-requests`, () =>
        HttpResponse.json({ code: "ERR_RATE_LIMITED" }, { status: 429 }),
      ),
    );

    const { container } = render(<SignupForm />);
    await user.type(mainSignupEmailInput(), "sam@acme.com");
    const panel = getPanel(container);
    await user.click(within(panel).getByRole("button", { name: /request access/i }));

    const status = await within(panel).findByRole("status");
    expect(status).toHaveTextContent(/you.?re going a little fast.*try again in a moment/i);
  });
});

describe("SignupForm — the 403 dead end is replaced, not merely reworded (M8, M9, M10)", () => {
  const user = userEvent.setup();

  it("test_403_invite_only_replaces_dead_end_preserves_fields", async () => {
    // covers: M8, M9, M10
    server.use(
      http.post(`${APP}/api/auth/signup`, () =>
        HttpResponse.json(
          {
            type: "about:blank",
            title: "Public signup is disabled; ask an existing member for an invite",
            status: 403,
            code: "ERR_SIGNUP_INVITE_ONLY",
          },
          { status: 403 },
        ),
      ),
    );

    const { container } = render(<SignupForm />);
    await fillMainSignup(user, { tenant_name: "Acme", email: "sam@acme.com", password: "hunter12345" });
    await user.click(screen.getByRole("button", { name: /sign up/i }));

    // The old dead-end copy is RETIRED for this specific code.
    await waitFor(() => {
      expect(screen.queryByText(/ask an existing member/i)).not.toBeInTheDocument();
    });

    // The panel takes its place (M1 was already unconditional; this proves it
    // survives a real 403 round trip too, and that M9's code-gate resolved).
    const panel = getPanel(container);
    expect(within(panel).getByText(/already have access another way\?/i)).toBeInTheDocument();

    // Typed values are preserved, not cleared, so Sam can reconsider without retyping.
    expect(screen.getByLabelText(/tenant name/i)).toHaveValue("Acme");
    expect(mainSignupEmailInput()).toHaveValue("sam@acme.com");
    expect(screen.getByLabelText(/password/i)).toHaveValue("hunter12345");
  });

  it("test_403_unrelated_code_keeps_generic_error_path", async () => {
    // covers: M9 (negative) — a 403 with a DIFFERENT code must NOT retire the
    // generic globalError path; that path stays byte-unchanged per §3's own
    // words ("any other 403/5xx keeps today's generic globalError path,
    // byte-unchanged"). See the file-level KNOWN TENSION note re: §2's
    // "panel is NOT shown" phrasing — this test follows §1 M1 + §3 instead,
    // asserting the panel remains present (M1 is unconditional) while the
    // generic error text ALSO renders for this non-matching code.
    server.use(
      http.post(`${APP}/api/auth/signup`, () =>
        HttpResponse.json(
          { type: "about:blank", title: "Something else broke", status: 403, code: "ERR_SOMETHING_UNRELATED" },
          { status: 403 },
        ),
      ),
    );

    const { container } = render(<SignupForm />);
    await fillMainSignup(user);
    await user.click(screen.getByRole("button", { name: /sign up/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/something else broke/i);
    });

    // M1's panel is unconditional — it must still be there alongside the generic error.
    const panel = getPanel(container);
    expect(within(panel).getByText(/already have access another way\?/i)).toBeInTheDocument();
  });
});

describe("SignupForm — panel accessibility", () => {
  const user = userEvent.setup();

  it("test_panel_a11y_axe_no_serious_violations", async () => {
    const { container } = render(<SignupForm />);
    // Assert the subject EXISTS before auditing it. Without this, axe audits a
    // SignupForm that simply has no panel, finds nothing, and the test passes
    // vacuously — staying green even if M1 were never built. §1 M1 renders the
    // panel unconditionally, so its absence must fail here too, not just in the
    // keyboard test below. (Orchestrator fix at TESTS review: this test was the
    // one green case in an otherwise-red suite, for exactly that wrong reason.)
    const panel = getPanel(container);
    const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
    const seriousCritical = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(seriousCritical).toEqual([]);
    // Guard against a future refactor making the panel empty-but-present.
    expect(panel.textContent?.trim()).not.toBe("");
  });

  it("test_panel_keyboard_reachable_in_order", async () => {
    // covers: accessibility-as-research scenario — SSO link, the request-access
    // email input, and its submit button are all reachable via Tab and
    // activatable via keyboard (Enter/Space), matching JoinByDomainForm's
    // existing all-keyboard-operable precedent.
    const { container } = render(<SignupForm />);
    const panel = getPanel(container);
    const ssoLink = within(panel).getByRole("link", { name: /sign in with sso instead/i });
    const requestInput = within(panel).getByRole("textbox");
    const requestButton = within(panel).getByRole("button", { name: /request access/i });

    const reachable = new Set<Element>();
    // Bound the walk so a broken tab order can't hang the test.
    for (let i = 0; i < 40 && reachable.size < 3; i++) {
      await user.tab();
      if (document.activeElement) reachable.add(document.activeElement);
    }

    expect(reachable.has(ssoLink)).toBe(true);
    expect(reachable.has(requestInput)).toBe(true);
    expect(reachable.has(requestButton)).toBe(true);
  });
});
