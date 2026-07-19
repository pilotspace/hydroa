/**
 * tests/signup-form-joined-outcome.test.tsx — RED suite for domain-auto-assign-login
 * TASK.md §3 v1, M5 (SignupForm differentiated joined-vs-created outcome).
 *
 * New file (does not edit tests/signup.test.tsx or tests/signup-account-type.test.tsx
 * — those suites' existing assertions, including the FROZEN
 * `expect(router.push).toHaveBeenCalledWith("/app/keys")` pin for a plain
 * `{ ok: true }` 201 body, stay byte-identical: this suite only adds NEW
 * assertions for the `joined_existing_tenant` field those tests never send).
 *
 * RED before Build: SignupForm's 201 handler unconditionally calls
 * `router.push("/app/keys")` and renders no outcome message at all — it never
 * reads `joined_existing_tenant` from the response body, so no "joined" text is
 * ever rendered.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { getRouterMock } from "./mocks/next-navigation";

import { SignupForm } from "@/components/auth/SignupForm";

describe("SignupForm — joined vs created outcome (M5)", () => {
  const user = userEvent.setup();

  async function fillAndSubmit(overrides?: { email?: string }) {
    await user.type(screen.getByLabelText(/tenant name/i), "Acme");
    await user.type(screen.getByLabelText(/email/i), overrides?.email ?? "ada@acme.io");
    await user.type(screen.getByLabelText(/password/i), "hunter12345");
    await user.click(screen.getByRole("button", { name: /sign up/i }));
  }

  it("test_signup_form_shows_joined_outcome_when_joining_existing_tenant", async () => {
    // covers: M5 — joined_existing_tenant: true renders a differentiated "joined" outcome
    server.use(
      http.post("http://localhost:3000/api/auth/signup", () =>
        HttpResponse.json({ ok: true, joined_existing_tenant: true }, { status: 201 }),
      ),
    );

    render(<SignupForm />);
    await fillAndSubmit({ email: "newhire@acme.com" });

    const outcome = await screen.findByText(/joined/i);
    expect(outcome).toBeInTheDocument();
  });

  it("test_signup_form_shows_created_outcome_when_new_tenant", async () => {
    // covers: M5 — joined_existing_tenant: false (or absent) keeps the workspace-created
    // outcome, and the existing immediate-redirect behavior is unchanged.
    server.use(
      http.post("http://localhost:3000/api/auth/signup", () =>
        HttpResponse.json({ ok: true, joined_existing_tenant: false }, { status: 201 }),
      ),
    );

    render(<SignupForm />);
    await fillAndSubmit({ email: "founder@brandnew.com" });

    // Never shows the "joined" outcome for a brand-new tenant.
    expect(screen.queryByText(/joined/i)).not.toBeInTheDocument();
    const router = getRouterMock();
    expect(router.push).toHaveBeenCalledWith("/app/keys");
  });
});
