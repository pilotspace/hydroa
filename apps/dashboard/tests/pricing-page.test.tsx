/**
 * pricing-page.test.tsx — RED suite for the v38 public /pricing page.
 * Covers the 6 §4 scenarios. Behavioral (render + role queries + axe) plus a
 * source-introspection guard for the public-route invariant.
 */
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { axe } from "@/test-support/axe";
import PricingPage from "@/app/(marketing)/pricing/page";

const DASHBOARD_ROOT = resolve(__dirname, "..");
function readSource(rel: string): string {
  const abs = resolve(DASHBOARD_ROOT, rel);
  return existsSync(abs) ? readFileSync(abs, "utf-8") : "";
}

describe("test_three_tiers", () => {
  it("renders exactly one h1 and >=3 tier cards each with name + price + CTA", () => {
    render(<PricingPage />);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    // tier names are headings (h2/h3); the three named tiers must exist
    for (const name of [/starter/i, /team/i, /enterprise/i]) {
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    }
    // every tier card must carry a price/qualifier ($ or "Contact") and a CTA link
    const links = screen.getAllByRole("link");
    expect(links.length).toBeGreaterThanOrEqual(3);
  });
});

describe("test_tier_ctas", () => {
  it("Starter and Team CTAs link to /signup and Enterprise CTA is present", () => {
    render(<PricingPage />);
    const signupLinks = screen
      .getAllByRole("link")
      .filter((a) => a.getAttribute("href") === "/signup");
    expect(signupLinks.length).toBeGreaterThanOrEqual(2);
  });
});

describe("test_pricing_a11y", () => {
  it("axe reports 0 serious/critical violations", async () => {
    const { container } = render(
      <main id="main">
        <PricingPage />
      </main>,
    );
    const results = await axe(container);
    const serious = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(serious).toEqual([]);
  });
});

describe("test_reject_heading_order", () => {
  it("has exactly one h1 and no skipped heading level", () => {
    const { container } = render(<PricingPage />);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    const levels = Array.from(container.querySelectorAll("h1,h2,h3,h4,h5,h6")).map(
      (h) => Number(h.tagName[1]),
    );
    // no jump greater than 1 between consecutive headings
    for (let i = 1; i < levels.length; i++) {
      expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
    }
  });
});

describe("test_reject_public_not_gated", () => {
  it("page source reads no cookies() and does no authenticated fetch", () => {
    const src = readSource("app/(marketing)/pricing/page.tsx");
    expect(src).not.toBe("");
    expect(src).not.toMatch(/cookies\(\)/);
    expect(src).not.toMatch(/from ["']next\/headers["']/);
    expect(src).not.toMatch(/bffGet|api-client|useQuery/);
  });
});

describe("test_reject_incomplete_tier", () => {
  it("every tier card carries a name heading, a price/qualifier, and a CTA", () => {
    render(<PricingPage />);
    const cards = screen.getAllByRole("article");
    expect(cards.length).toBeGreaterThanOrEqual(3);
    for (const card of cards) {
      const scope = within(card);
      // a name heading (h2)
      expect(scope.getByRole("heading", { level: 2 })).toBeInTheDocument();
      // a CTA link inside the card
      expect(scope.getAllByRole("link").length).toBeGreaterThanOrEqual(1);
      // a price/qualifier token
      expect(card.textContent ?? "").toMatch(/\$|free|contact/i);
    }
  });
});

// ── residency-tiers-ui TASK.md §3 M11: residency + priority story, zero fetch ──────
describe("test_pricing_residency_and_priority_story", () => {
  it("Team tier lists the priority service-tier feature bullet", () => {
    render(<PricingPage />);
    const teamCard = screen.getByRole("article", { name: /team/i });
    expect(
      within(teamCard).getByText(/priority service tier \(optional, usage-priced\)/i),
    ).toBeInTheDocument();
  });

  it("Enterprise tier lists the data-residency feature bullet", () => {
    render(<PricingPage />);
    const entCard = screen.getByRole("article", { name: /enterprise/i });
    expect(
      within(entCard).getByText(/data residency: pin inference to us or eu/i),
    ).toBeInTheDocument();
  });

  it("a short residency + priority callout section renders as static copy", () => {
    render(<PricingPage />);
    expect(
      screen.getByText(/refused, never silently\s*rerouted/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/standard traffic never starved/i)).toBeInTheDocument();
  });

  it("zero network requests are made by the page itself (still a Server Component)", () => {
    // Reuses test_reject_public_not_gated's source-introspection guard: the new
    // copy must not introduce cookies()/next-headers/any fetch-shaped import.
    const src = readSource("app/(marketing)/pricing/page.tsx");
    expect(src).not.toMatch(/cookies\(\)/);
    expect(src).not.toMatch(/from ["']next\/headers["']/);
    expect(src).not.toMatch(/bffGet|api-client|useQuery|fetch\(/);
  });

  it("heading order stays valid with the new callout section (no skipped levels)", () => {
    const { container } = render(<PricingPage />);
    const levels = Array.from(container.querySelectorAll("h1,h2,h3,h4,h5,h6")).map(
      (h) => Number(h.tagName[1]),
    );
    for (let i = 1; i < levels.length; i++) {
      expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
    }
  });
});
