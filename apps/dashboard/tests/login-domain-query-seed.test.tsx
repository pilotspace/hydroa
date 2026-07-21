/**
 * tests/login-domain-query-seed.test.tsx — RED suite for `signup-refusal-router`
 * TASK.md §3 (FROZEN @ v1), M2's second half: LoginForm reads a `?domain=`
 * search param ONCE (mirroring its existing one-shot localStorage seed effect,
 * see tests/sso-login.test.tsx:test_sso_prefills_last_domain) to pre-fill
 * "Work email or domain" when a visitor arrives from the signup routing
 * panel's SSO link.
 *
 * Mocking convention mirrors tests/joined-workspace-callout.test.tsx's own
 * established precedent for stabilizing the shared next/navigation
 * useSearchParams mock (tests/setup.ts) to a single instance per test.
 *
 * RED reason (today): LoginForm never calls useSearchParams() at all — it
 * only seeds ssoDomain from localStorage in a useEffect. A `?domain=acme.com`
 * search param today has zero effect on the rendered "Work email or domain"
 * field, so this test's `toHaveValue("acme.com")` assertion fails (the field
 * stays empty).
 *
 * OPEN QUESTION (not tested here — not specified by §3): precedence between a
 * `?domain=` param and an existing localStorage["sso_domain"] value when BOTH
 * are present. §3 only says LoginForm reads the param "to pre-fill", mirroring
 * the localStorage seed's ONE-SHOT shape; it does not say which wins if both
 * exist. This suite only covers the unambiguous case (param present,
 * localStorage empty) — flagged for BUILD/VERIFY, not silently decided.
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { useSearchParams } from "next/navigation";
import { LoginForm } from "@/components/auth/LoginForm";

function setDomainParam(value: string | null) {
  const params = new URLSearchParams();
  if (value !== null) params.set("domain", value);
  vi.mocked(useSearchParams).mockReturnValue(params as ReturnType<typeof useSearchParams>);
}

beforeEach(() => {
  setDomainParam(null);
  localStorage.clear();
});

describe("LoginForm — SSO domain seeded from a ?domain= query param (M2)", () => {
  it("test_login_prefills_from_domain_query_param", async () => {
    // covers: M2, M6 — arriving at /login?domain=acme.com pre-fills the single
    // merged "Email" field with "acme.com", with no stored localStorage value
    // to conflict. Retarget (merge-login-email-field §3): selector only — the
    // seeded value now pre-fills the SAME field password login also uses (⚠
    // disclosed at CONTRACT); the prefill guarantee itself is intact.
    setDomainParam("acme.com");
    render(<LoginForm />);

    await waitFor(() =>
      expect(screen.getByLabelText(/^email$/i)).toHaveValue("acme.com"),
    );
  });

  it("test_login_no_domain_param_no_crash_falls_back_to_existing_seed", async () => {
    // covers: M2 (guard) — absent param must not throw or clobber the existing
    // localStorage seed path (tests/sso-login.test.tsx already pins that path
    // byte-unchanged; this only proves the new read degrades safely to empty).
    // Retarget: selector only.
    setDomainParam(null);
    render(<LoginForm />);

    await waitFor(() =>
      expect(screen.getByLabelText(/^email$/i)).toHaveValue(""),
    );
  });
});
