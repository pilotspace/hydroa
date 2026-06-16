/**
 * auth-pages-redesign.test.tsx — RED suite for the v23 auth-pages-redesign task.
 *
 * Frozen §3 (presentation-only): /login + /signup render through a shared `AuthShell`
 * (split-screen decorative brand panel + content column); each form's fields are wrapped
 * in a token-styled Card marked [data-slot="auth-card"] using DS Input/Button; the SSO entry
 * is styled via `Button asChild` (still an <a>). NO behavior changes — the dense frozen
 * login/signup/oidc suites remain the regression net; this file asserts ONLY the adoption.
 *
 * TRUE-RED REASON: @/components/ui/auth-shell does not exist yet (MODULE_NOT_FOUND), and
 * LoginForm/SignupForm still render raw unstyled HTML with no [data-slot="auth-card"] and an
 * unstyled SSO anchor — so the AuthShell import and the data-slot / styled-SSO assertions fail
 * until Build adds AuthShell and restyles the forms.
 */

import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { axe, axeMatchers } from "@/test-support/axe";

import { AuthShell } from "@/components/ui/auth-shell";
import { LoginForm } from "@/components/auth/LoginForm";
import { SignupForm } from "@/components/auth/SignupForm";
import LoginPage from "@/app/(auth)/login/page";
import SignupPage from "@/app/(auth)/signup/page";

expect.extend(axeMatchers);

declare module "vitest" {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  interface Assertion<T = any> {
    toHaveNoViolations(): T;
  }
  interface AsymmetricMatchersContaining {
    toHaveNoViolations(): unknown;
  }
}

const FOCUSABLE = 'a, button, input, select, textarea, [tabindex]';
const HEADINGS = "h1, h2, h3, h4, h5, h6";

describe("AuthShell — decorative brand panel + content column", () => {
  it("test_auth_shell_brand_panel_decorative", () => {
    const { container } = render(
      <AuthShell>
        <h1>Sign in to your account</h1>
        <form aria-label="Log in">
          <input aria-label="email" />
        </form>
      </AuthShell>,
    );

    const brand = container.querySelector('[data-slot="auth-brand"]');
    expect(brand).not.toBeNull();
    // Decorative: removed from the a11y tree, with no focusable child and no heading.
    expect(brand).toHaveAttribute("aria-hidden", "true");
    expect(brand!.querySelectorAll(FOCUSABLE).length).toBe(0);
    expect(brand!.querySelectorAll(HEADINGS).length).toBe(0);

    // The only heading on the surface is the page heading passed as a child.
    const headings = screen.getAllByRole("heading");
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent("Sign in to your account");

    // Children render (content column hosts the form).
    expect(screen.getByRole("form", { name: /log in/i })).toBeInTheDocument();
  });

  it("test_auth_shell_axe_clean", async () => {
    const { container } = render(
      <AuthShell>
        <h1>Create your account</h1>
        <form aria-label="Sign up">
          <label htmlFor="x">Email</label>
          <input id="x" />
        </form>
      </AuthShell>,
    );
    // color-contrast cannot run under jsdom (no layout) — the real-Chromium e2e-a11y
    // spec covers contrast; here we catch aria-hidden-focus / heading-order / landmark.
    const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results).toHaveNoViolations();
  });
});

describe("LoginForm — Card-wrapped fields + styled SSO", () => {
  it("test_login_form_card_and_styled_sso", () => {
    const { container } = render(<LoginForm />);

    // Fields live inside the token-styled auth Card.
    const card = container.querySelector('[data-slot="auth-card"]');
    expect(card).not.toBeNull();
    expect(within(card as HTMLElement).getByLabelText(/email/i)).toBeInTheDocument();
    expect(within(card as HTMLElement).getByLabelText(/password/i)).toBeInTheDocument();

    // Submit is the DS Button (frozen name preserved).
    expect(screen.getByRole("button", { name: /^log in$/i })).toBeInTheDocument();

    // SSO stays an <a> (full-page nav) but is styled via buttonVariants.
    const sso = screen.getByRole("link", { name: /sso/i });
    expect(sso.tagName).toBe("A");
    expect(sso).toHaveAttribute("href", "/api/auth/oidc/login");
    expect(sso.className).toContain("inline-flex"); // buttonVariants base marker
  });
});

describe("SignupForm — Card-wrapped fields, no SSO", () => {
  it("test_signup_form_card_no_sso", () => {
    const { container } = render(<SignupForm />);

    const card = container.querySelector('[data-slot="auth-card"]');
    expect(card).not.toBeNull();
    expect(within(card as HTMLElement).getByLabelText(/tenant name/i)).toBeInTheDocument();
    expect(within(card as HTMLElement).getByLabelText(/email/i)).toBeInTheDocument();
    expect(within(card as HTMLElement).getByLabelText(/password/i)).toBeInTheDocument();

    expect(screen.getByRole("button", { name: /^sign up$/i })).toBeInTheDocument();
    // Signup has NO SSO entry.
    expect(screen.queryByRole("link", { name: /sso/i })).toBeNull();
  });
});

describe("Auth pages render through AuthShell", () => {
  it("test_login_page_uses_authshell", async () => {
    const { container } = render(await LoginPage({ searchParams: Promise.resolve({}) }));

    expect(container.querySelector('[data-slot="auth-brand"]')).not.toBeNull();
    // Exactly one form landmark on the page.
    expect(container.querySelectorAll("form")).toHaveLength(1);
    // Page heading unchanged, and it is the sole h1 (no h1→h3 skip in the form card).
    expect(screen.getByRole("heading", { level: 1, name: /sign in to your account/i })).toBeInTheDocument();
    const card = container.querySelector('[data-slot="auth-card"]');
    expect(card!.querySelectorAll("h3").length).toBe(0);
  });

  it("test_signup_page_uses_authshell", () => {
    const { container } = render(<SignupPage />);

    expect(container.querySelector('[data-slot="auth-brand"]')).not.toBeNull();
    expect(container.querySelectorAll("form")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: /create your account/i })).toBeInTheDocument();
  });
});
