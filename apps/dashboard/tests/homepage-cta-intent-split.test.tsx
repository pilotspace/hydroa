/**
 * tests/homepage-cta-intent-split.test.tsx — RED suite for
 * `homepage-cta-intent-split` TASK.md §3 (FROZEN @ v1).
 *
 * Covers the §2 scenarios not already covered by a frozen suite:
 *   - the new "For your team" CTA (hero + final band), M2
 *   - the existing "Get started"/"Log in" CTAs stay byte-identical, M1/R3
 *     (landing-page.test.tsx:test_hero + landing-fidelity.test.tsx already
 *     regression-pin these; this suite adds the two-site + label-disjointness
 *     assertions those frozen suites don't make)
 *   - copy never promises a mechanism it can't keep, M3
 *   - SignupForm's one-shot `?account_type=business` preselect, M4/R1
 *   - the preselect never varies by domain/account state + issues no IO, M5/R2
 *   - the homepage stays a pure Server Component with a static literal href, M6
 *   - existing SignupForm suites keep passing untouched, M8 (proven by CI, not
 *     re-asserted here — see the full-suite run this task requires)
 *   - EDGE: duplicate account_type params resolve deterministically
 *   - EDGE: a manual radio click after mount always wins (one-shot effect)
 *   - ACCESSIBILITY-AS-RESEARCH: hero CTAs stay keyboard-reachable in order
 *
 * RED reason (today, before Build): page.tsx has no "For your team" link in
 * either the hero row or the final CTA band, and SignupForm never calls
 * useSearchParams() at all — so every M2/M4-covering assertion below fails
 * for the right reason (missing implementation), not a broken harness.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useSearchParams } from "next/navigation";

import MarketingRootPage from "@/app/(marketing)/page";
import { SignupForm } from "@/components/auth/SignupForm";

const DASHBOARD_ROOT = resolve(__dirname, "..");

function readSource(relPath: string): string {
  return readFileSync(resolve(DASHBOARD_ROOT, relPath), "utf-8");
}

function setAccountTypeParam(query: string | null) {
  const params = new URLSearchParams(query ?? "");
  vi.mocked(useSearchParams).mockReturnValue(params as ReturnType<typeof useSearchParams>);
}

beforeEach(() => {
  setAccountTypeParam(null);
});

// ─────────────────────────────────────────────────────────────────────────
// M1/R3 — the existing personal CTA stays byte-identical in BOTH sites
// ─────────────────────────────────────────────────────────────────────────
describe("homepage — personal 'Get started' CTA stays byte-identical (M1, R3)", () => {
  it("test_get_started_present_hero_and_final_band_unchanged", () => {
    const { container } = render(<MarketingRootPage />);
    const getStartedLinks = screen
      .getAllByRole("link", { name: /get started/i })
      .filter((l) => l.getAttribute("href") === "/signup");
    // hero row + final CTA band = 2 byte-identical "Get started" -> /signup links
    expect(getStartedLinks.length).toBeGreaterThanOrEqual(2);

    const loginLinks = screen
      .getAllByRole("link", { name: /log in/i })
      .filter((l) => l.getAttribute("href") === "/login");
    expect(loginLinks.length).toBeGreaterThanOrEqual(1);
    void container;
  });
});

// ─────────────────────────────────────────────────────────────────────────
// M2 — the NEW team-intent CTA appears in both the hero row and final band
// ─────────────────────────────────────────────────────────────────────────
describe("homepage — 'For your team' CTA appears in hero AND final band (M2)", () => {
  it("test_team_cta_present_both_sites_with_exact_href", () => {
    render(<MarketingRootPage />);
    const teamLinks = screen.getAllByRole("link", {
      name: (name) => /for your team/i.test(name),
    });
    expect(teamLinks.length).toBeGreaterThanOrEqual(2);
    for (const link of teamLinks) {
      expect(link.getAttribute("href")).toBe("/signup?account_type=business");
      // the label must never collide with the frozen "get started" query (R-frozen-1)
      expect(link.textContent ?? "").not.toMatch(/get started/i);
    }
  });

  it("test_team_cta_label_never_matches_get_started_query", () => {
    render(<MarketingRootPage />);
    // The frozen landing-page.test.tsx:test_hero .find()s over ALL "get
    // started"-named links expecting href==="/signup" exactly — prove the new
    // link's accessible name can never be swept into that query.
    const getStartedNamed = screen.getAllByRole("link", { name: /get started/i });
    for (const link of getStartedNamed) {
      expect(link.getAttribute("href")).toBe("/signup");
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────
// M3 — neither CTA promises a mechanism it can't keep
// ─────────────────────────────────────────────────────────────────────────
describe("homepage — CTA copy never promises a mechanism it can't keep (M3)", () => {
  it("test_no_instant_or_guaranteed_account_promise_in_hero_or_band", () => {
    const { container } = render(<MarketingRootPage />);
    const hero = container.querySelector('[aria-labelledby="hero-heading"]');
    const finalBand = container.querySelector('[aria-labelledby="cta-heading"]');
    expect(hero).not.toBeNull();
    expect(finalBand).not.toBeNull();
    const text = `${hero!.textContent ?? ""} ${finalBand!.textContent ?? ""}`;
    expect(text).not.toMatch(/instant/i);
    expect(text).not.toMatch(/guarantee/i);
    expect(text).not.toMatch(/right away/i);
  });
});

// ─────────────────────────────────────────────────────────────────────────
// M4/R1 — SignupForm's one-shot account_type preselect
// ─────────────────────────────────────────────────────────────────────────
describe("SignupForm — one-shot ?account_type= preselect (M4, R1)", () => {
  it("test_business_param_preselects_business_on_first_render", async () => {
    setAccountTypeParam("account_type=business");
    render(<SignupForm />);

    expect(await screen.findByRole("radio", { name: /business/i })).toBeChecked();
    expect(screen.getByRole("radio", { name: /personal/i })).not.toBeChecked();
  });

  it("test_absent_param_leaves_personal_default", async () => {
    setAccountTypeParam(null);
    render(<SignupForm />);

    expect(await screen.findByRole("radio", { name: /personal/i })).toBeChecked();
    expect(screen.getByRole("radio", { name: /business/i })).not.toBeChecked();
  });

  it("test_malformed_account_type_falls_back_to_personal", async () => {
    setAccountTypeParam("account_type=enterprise");
    render(<SignupForm />);

    expect(await screen.findByRole("radio", { name: /personal/i })).toBeChecked();
    expect(screen.getByRole("radio", { name: /business/i })).not.toBeChecked();
  });

  it("test_empty_account_type_value_falls_back_to_personal", async () => {
    setAccountTypeParam("account_type=");
    render(<SignupForm />);

    expect(await screen.findByRole("radio", { name: /personal/i })).toBeChecked();
    expect(screen.getByRole("radio", { name: /business/i })).not.toBeChecked();
  });

  it("test_duplicate_account_type_params_resolve_to_first_value_deterministically", async () => {
    // EDGE — URLSearchParams.get() returns the FIRST value per WHATWG spec.
    setAccountTypeParam("account_type=business&account_type=personal");
    render(<SignupForm />);

    expect(await screen.findByRole("radio", { name: /business/i })).toBeChecked();
    expect(screen.getByRole("radio", { name: /personal/i })).not.toBeChecked();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// EDGE — a manual radio click after mount always wins (one-shot effect)
// ─────────────────────────────────────────────────────────────────────────
describe("SignupForm — a manual radio click after mount always wins", () => {
  it("test_manual_personal_click_overrides_business_preselect_and_never_reverts", async () => {
    const user = userEvent.setup();
    setAccountTypeParam("account_type=business");
    render(<SignupForm />);

    expect(await screen.findByRole("radio", { name: /business/i })).toBeChecked();

    await user.click(screen.getByRole("radio", { name: /personal/i }));
    expect(screen.getByRole("radio", { name: /personal/i })).toBeChecked();
    expect(screen.getByRole("radio", { name: /business/i })).not.toBeChecked();

    // The one-shot effect has [] deps — nothing re-fires to clobber the
    // manual choice on a subsequent render/rerender.
    await screen.findByLabelText(/tenant name/i);
    expect(screen.getByRole("radio", { name: /personal/i })).toBeChecked();
    expect(screen.getByRole("radio", { name: /business/i })).not.toBeChecked();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// M5/R2 — the preselect never varies by account/domain state, no new IO
// ─────────────────────────────────────────────────────────────────────────
describe("SignupForm — the preselect is a pure function of the query string only (M5, R2)", () => {
  it("test_preselect_identical_regardless_of_typed_email_domain", async () => {
    const user = userEvent.setup();
    setAccountTypeParam("account_type=business");
    const { unmount } = render(<SignupForm />);
    expect(await screen.findByRole("radio", { name: /business/i })).toBeChecked();
    // typing a "known corporate" vs "unknown" domain must never change the
    // preselected account_type — it is a pure function of the literal param.
    await user.type(screen.getByLabelText(/^email$/i), "person@some-unknown-domain.example");
    expect(screen.getByRole("radio", { name: /business/i })).toBeChecked();
    unmount();

    setAccountTypeParam("account_type=business");
    render(<SignupForm />);
    expect(await screen.findByRole("radio", { name: /business/i })).toBeChecked();
  });

  it("test_preselect_issues_no_fetch_call", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    setAccountTypeParam("account_type=business");
    render(<SignupForm />);
    expect(await screen.findByRole("radio", { name: /business/i })).toBeChecked();
    // Mounting + preselecting must never itself issue any network request —
    // the only fetch calls this component makes are on explicit submit
    // actions, none of which happened here.
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// M6 — the homepage stays a pure Server Component; the new link is static
// ─────────────────────────────────────────────────────────────────────────
describe("homepage — stays a pure Server Component; the new CTA is a static literal (M6)", () => {
  it("test_new_cta_is_a_static_literal_href_not_client_state", () => {
    const src = readSource("app/(marketing)/page.tsx");
    expect(src).not.toMatch(/"use client"/);
    expect(src).not.toMatch(/cookies\(\)/);
    // the new href is a literal string in source, not an interpolated/derived value
    expect(src).toMatch(/href="\/signup\?account_type=business"/);
  });
});

// ─────────────────────────────────────────────────────────────────────────
// ACCESSIBILITY-AS-RESEARCH — hero CTAs stay keyboard-reachable in order
// ─────────────────────────────────────────────────────────────────────────
describe("homepage — hero CTAs remain keyboard-reachable in a sensible order (heuristic)", () => {
  it("test_hero_cta_tab_order_get_started_then_team_then_login", () => {
    const { container } = render(<MarketingRootPage />);
    const hero = container.querySelector('[aria-labelledby="hero-heading"]');
    expect(hero).not.toBeNull();
    const ctaRow = within(hero as HTMLElement).getAllByRole("link", {
      name: /get started|for your team|log in/i,
    });
    expect(ctaRow.length).toBeGreaterThanOrEqual(3);
    const names = ctaRow.map((l) => l.textContent?.trim().toLowerCase() ?? "");
    const getStartedIdx = names.findIndex((n) => n.includes("get started"));
    const teamIdx = names.findIndex((n) => n.includes("for your team"));
    const loginIdx = names.findIndex((n) => n.includes("log in"));
    expect(getStartedIdx).toBeGreaterThanOrEqual(0);
    expect(teamIdx).toBeGreaterThan(getStartedIdx);
    expect(loginIdx).toBeGreaterThan(teamIdx);
    // every CTA in the row stays keyboard-activatable (no tabindex="-1")
    for (const link of ctaRow) {
      expect(link.getAttribute("tabindex")).not.toBe("-1");
    }
  });
});
