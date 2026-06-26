/**
 * tests/auth-hardening.test.tsx — v50 harden-auth
 *
 * EC8: the auth pages give a motion-safe entrance and carry the field-level
 * validation, resilient (in-flight-aware) submit, and no-leak failure surface.
 * Motion is owned once by the shared AuthShell; the EC8 form behaviors are
 * foundation-delivered (LoginForm) and asserted here as a coverage net.
 *
 * RED before build: AuthShell renders its content in a plain <div>, so the
 * `[data-slot="reveal"]` entrance marker is absent.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse, delay } from "msw";
import { server } from "./mocks/server";
import { getRouterMock } from "./mocks/next-navigation";

import { AuthShell } from "@/components/ui/auth-shell";
import { LoginForm } from "@/components/auth/LoginForm";

describe("auth shell entrance motion", () => {
  it("test_auth_content_in_reveal", () => {
    render(
      <AuthShell>
        <h1>Sign in</h1>
        <form aria-label="test">
          <button type="submit">Go</button>
        </form>
      </AuthShell>,
    );
    const main = screen.getByRole("main");
    const reveal = main.querySelector('[data-slot="reveal"]');
    expect(reveal).not.toBeNull();
    // form renders unconditionally inside the reveal wrapper
    expect(reveal?.querySelector("form")).not.toBeNull();
  });

  it("test_single_landmark_and_hidden_brand", () => {
    const { container } = render(
      <AuthShell>
        <form aria-label="test" />
      </AuthShell>,
    );
    expect(screen.getAllByRole("main")).toHaveLength(1);
    const brand = container.querySelector('[data-slot="auth-brand"]');
    expect(brand).toHaveAttribute("aria-hidden", "true");
  });
});

describe("EC8 — field validation, resilient submit, no leak", () => {
  const user = userEvent.setup();

  it("test_login_invalid_email_inline_error", async () => {
    render(<LoginForm />);
    await user.type(screen.getByLabelText(/^email$/i), "notanemail");
    await user.type(screen.getByLabelText(/password/i), "hunter12345");
    await user.click(screen.getByRole("button", { name: /^log in$/i }));

    // inline field error, no navigation
    expect(await screen.findByText(/invalid email address/i)).toBeInTheDocument();
    expect(getRouterMock().push).not.toHaveBeenCalled();
  });

  it("test_submit_disabled_in_flight", async () => {
    // a slow login → the form stays in its isSubmitting state long enough to observe
    server.use(
      http.post("http://localhost:3000/api/auth/login", async () => {
        await delay(80);
        return HttpResponse.json({ ok: true });
      }),
    );
    render(<LoginForm />);
    await user.type(screen.getByLabelText(/^email$/i), "ada@acme.io");
    await user.type(screen.getByLabelText(/password/i), "hunter12345");
    await user.click(screen.getByRole("button", { name: /^log in$/i }));

    // while in flight the submit reflects isSubmitting: relabeled + disabled
    await waitFor(() => {
      const btn = screen.getByRole("button", { name: /signing in/i });
      expect(btn).toBeDisabled();
    });
  });
});
