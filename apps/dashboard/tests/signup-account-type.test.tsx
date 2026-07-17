/**
 * signup-account-type.test.tsx — RED suite for activation-quickstart TASK.md §3 v1,
 * M1 (SignupForm account_type control, client half).
 *
 * RED before Build: SignupForm has no account_type control/field yet, so the POST
 * body never carries account_type — these assertions fail on the CAPTURED body,
 * not on module resolution (SignupForm already exists from a prior task).
 *
 * New file (does not edit tests/signup.test.tsx — that suite's 3 existing tests
 * stay byte-identical). Mirrors its own render/fillAndSubmit conventions.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { getRouterMock } from "./mocks/next-navigation";

import { SignupForm } from "@/components/auth/SignupForm";

describe("SignupForm — account_type (activation-quickstart)", () => {
  const user = userEvent.setup();

  async function fill(overrides?: { tenant_name?: string; email?: string; password?: string }) {
    await user.type(screen.getByLabelText(/tenant name/i), overrides?.tenant_name ?? "Acme");
    await user.type(screen.getByLabelText(/email/i), overrides?.email ?? "ada@acme.io");
    await user.type(screen.getByLabelText(/password/i), overrides?.password ?? "hunter12345");
  }

  /**
   * Scenario: Personal signup sends account_type and reaches the free plan (M1, M3 client half)
   */
  it("test_signup_personal_default_account_type_sent", async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post("http://localhost:3000/api/auth/signup", async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ok: true }, { status: 201 });
      })
    );

    render(<SignupForm />);

    // "personal" is pre-selected — no interaction with the control needed.
    expect(screen.getByRole("radio", { name: /personal/i })).toBeChecked();

    await fill();
    await user.click(screen.getByRole("button", { name: /sign up/i }));

    await screen.findByRole("radio", { name: /personal/i }); // settle a tick
    expect(capturedBody).not.toBeNull();
    const body = capturedBody as unknown as Record<string, unknown>;
    expect(body.account_type).toBe("personal");

    // 201 -> redirect unchanged
    const router = getRouterMock();
    expect(router.push).toHaveBeenCalledWith("/app/keys");
  });

  /**
   * Scenario: Business signup stays byte-identical (M1)
   */
  it("test_signup_business_selection_sent", async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post("http://localhost:3000/api/auth/signup", async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ok: true }, { status: 201 });
      })
    );

    render(<SignupForm />);

    await user.click(screen.getByRole("radio", { name: /business/i }));
    expect(screen.getByRole("radio", { name: /business/i })).toBeChecked();
    expect(screen.getByRole("radio", { name: /personal/i })).not.toBeChecked();

    await fill();
    await user.click(screen.getByRole("button", { name: /sign up/i }));

    await screen.findByRole("radio", { name: /business/i });
    expect(capturedBody).not.toBeNull();
    const body = capturedBody as unknown as Record<string, unknown>;
    expect(body.account_type).toBe("business");
  });
});
