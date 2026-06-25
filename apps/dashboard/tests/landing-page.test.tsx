/**
 * landing-page.test.tsx — RED suite for the v38 landing page content task
 *
 * Covers all 7 §4 test-plan items from TASK.md (frozen contract v1).
 * All tests MUST fail for the right reason (missing implementation) before Build.
 *
 * Suite: "legacy" project (tests/ directory, tests/setup.ts).
 *
 * RED expectations (before the page is filled out):
 *   - test_hero:                  page.tsx currently lacks #product/#pricing/#docs sections
 *   - test_feature_grid:          page.tsx currently has no feature cards
 *   - test_nav_anchors_resolve:   page.tsx missing sections with ids product/pricing/docs
 *   - test_landing_a11y:          page.tsx missing required sections
 *   - test_reject_heading_order:  page.tsx only has one h1 currently — but needs all h2s too
 *   - test_reject_dangling_anchor: page.tsx missing section ids
 *   - test_reject_public_not_gated: source check on (marketing)/page.tsx
 */

import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { axe } from "@/test-support/axe";
import React from "react";

// ── Helpers ────────────────────────────────────────────────────────────────────
const DASHBOARD_ROOT = resolve(__dirname, "..");

function readSource(relPath: string): string {
  const abs = resolve(DASHBOARD_ROOT, relPath);
  if (!existsSync(abs)) return "";
  return readFileSync(abs, "utf-8");
}

// Lazy import the landing page component
async function renderLanding() {
  // We need to render the page in isolation (without the shell's header/footer,
  // which is tested in marketing-shell.test.tsx). The page renders a <main> block.
  const mod = await import("@/app/(marketing)/page");
  const Page = mod.default;
  const { container } = render(<Page />);
  return container;
}

// ─────────────────────────────────────────────────────────────────────────────
// test_hero
// Exactly one h1; "Get started" -> /signup; "Log in" -> /login
// ─────────────────────────────────────────────────────────────────────────────
describe("test_hero", () => {
  it("renders exactly one h1", async () => {
    const container = await renderLanding();
    const h1s = container.querySelectorAll("h1");
    expect(h1s.length).toBe(1);
  });

  it("has a 'Get started' link pointing to /signup", async () => {
    await renderLanding();
    const links = screen.getAllByRole("link", { name: /get started/i });
    expect(links.length).toBeGreaterThanOrEqual(1);
    // At least one must point to /signup
    const signupLink = links.find((l) => l.getAttribute("href") === "/signup");
    expect(signupLink).toBeDefined();
  });

  it("has a 'Log in' link pointing to /login", async () => {
    await renderLanding();
    const loginLinks = screen.getAllByRole("link", { name: /log in/i });
    expect(loginLinks.length).toBeGreaterThanOrEqual(1);
    const loginLink = loginLinks.find((l) => l.getAttribute("href") === "/login");
    expect(loginLink).toBeDefined();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_feature_grid
// #product section has >=4 feature cards
// ─────────────────────────────────────────────────────────────────────────────
describe("test_feature_grid", () => {
  it("#product section exists", async () => {
    const container = await renderLanding();
    const productSection = container.querySelector("#product");
    expect(productSection).not.toBeNull();
  });

  it("#product section has at least 4 feature cards", async () => {
    const container = await renderLanding();
    const productSection = container.querySelector("#product");
    expect(productSection).not.toBeNull();
    // Cards are rendered as divs with rounded-lg border (the Card primitive)
    // We check for at least 4 child elements that look like cards.
    // The feature grid will use Card components which render as styled divs.
    // We rely on data-testid or role-based lookup within the section.
    // Card components render as divs — count all card-like divs with role or text content.
    // We assert at least 4 headings within the #product section (one h3 per feature tile).
    const featureHeadings = productSection!.querySelectorAll("h3");
    expect(featureHeadings.length).toBeGreaterThanOrEqual(4);
  });

  it("#product section references README capabilities", async () => {
    const container = await renderLanding();
    const productSection = container.querySelector("#product");
    expect(productSection).not.toBeNull();
    const text = productSection!.textContent ?? "";
    // Must mention at least some of: routing, cost, key, rate-limit/bandwidth, analytics/spend, alert
    const capabilityTerms = [
      /routing/i,
      /cost/i,
      /key/i,
      /rate.?limit|bandwidth/i,
      /analytic|spend/i,
      /alert/i,
    ];
    const matchCount = capabilityTerms.filter((r) => r.test(text)).length;
    expect(matchCount).toBeGreaterThanOrEqual(4);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_nav_anchors_resolve
// Section ids product, pricing, and docs all exist (no dangling anchor)
// ─────────────────────────────────────────────────────────────────────────────
describe("test_nav_anchors_resolve", () => {
  it("section with id='product' exists", async () => {
    const container = await renderLanding();
    expect(container.querySelector("#product")).not.toBeNull();
  });

  it("section with id='pricing' exists", async () => {
    const container = await renderLanding();
    expect(container.querySelector("#pricing")).not.toBeNull();
  });

  it("section with id='docs' exists", async () => {
    const container = await renderLanding();
    expect(container.querySelector("#docs")).not.toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_landing_a11y
// axe 0 serious/critical; monotonic headings; landmarks present
// ─────────────────────────────────────────────────────────────────────────────
describe("test_landing_a11y", () => {
  it("landing passes axe serious/critical (WCAG-AA)", async () => {
    const container = await renderLanding();
    const results = await axe(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    const seriousCritical = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(seriousCritical).toEqual([]);
  });

  it("headings are monotonic: h1 before h2, no h3 before h2", async () => {
    const container = await renderLanding();
    const headings = Array.from(container.querySelectorAll("h1,h2,h3,h4,h5,h6"));
    const levels = headings.map((h) => parseInt(h.tagName.slice(1)));
    // First heading must be h1
    expect(levels[0]).toBe(1);
    // No level should jump by more than 1 from the previous
    for (let i = 1; i < levels.length; i++) {
      expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_reject_heading_order
// Exactly one h1 and no skipped heading level
// ─────────────────────────────────────────────────────────────────────────────
describe("test_reject_heading_order", () => {
  it("exactly one h1 exists on the landing page", async () => {
    const container = await renderLanding();
    const h1s = container.querySelectorAll("h1");
    expect(h1s.length).toBe(1);
  });

  it("no heading level is skipped (e.g. h1 -> h3 is rejected)", async () => {
    const container = await renderLanding();
    const headings = Array.from(container.querySelectorAll("h1,h2,h3,h4,h5,h6"));
    const levels = headings.map((h) => parseInt(h.tagName.slice(1)));
    for (let i = 1; i < levels.length; i++) {
      const diff = levels[i] - levels[i - 1];
      expect(
        diff,
        `Heading level skipped: h${levels[i - 1]} → h${levels[i]}`,
      ).toBeLessThanOrEqual(1);
    }
  });

  it("there are multiple h2 sections (hero uses h1, sections use h2)", async () => {
    const container = await renderLanding();
    const h2s = container.querySelectorAll("h2");
    // We expect: Features + Pricing teaser + Docs teaser + Built for enterprise + final CTA = 5
    expect(h2s.length).toBeGreaterThanOrEqual(3);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_reject_dangling_anchor
// Every shell nav anchor (#product/#pricing/#docs) has a matching section id
// ─────────────────────────────────────────────────────────────────────────────
describe("test_reject_dangling_anchor", () => {
  const REQUIRED_IDS = ["product", "pricing", "docs"] as const;

  for (const id of REQUIRED_IDS) {
    it(`section id="${id}" exists (shell nav anchor /#${id} must not dangle)`, async () => {
      const container = await renderLanding();
      const section = container.querySelector(`#${id}`);
      expect(
        section,
        `Missing section with id="${id}" — shell nav anchor /#${id} would dangle`,
      ).not.toBeNull();
    });
  }

  it("all three shell nav anchor ids resolve to a <section> element", async () => {
    const container = await renderLanding();
    for (const id of REQUIRED_IDS) {
      const el = container.querySelector(`section#${id}`);
      expect(
        el,
        `Expected <section id="${id}"> but got ${container.querySelector(`#${id}`)?.tagName ?? "nothing"}`,
      ).not.toBeNull();
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_reject_public_not_gated
// Source of (marketing)/page.tsx must not read cookies() or do authed fetch
// ─────────────────────────────────────────────────────────────────────────────
describe("test_reject_public_not_gated", () => {
  it("(marketing)/page.tsx source does not import cookies from next/headers", () => {
    const src = readSource("app/(marketing)/page.tsx");
    expect(src).not.toBe(""); // file must exist
    expect(src).not.toMatch(/from ["']next\/headers["']/);
  });

  it("(marketing)/page.tsx source does not call cookies()", () => {
    const src = readSource("app/(marketing)/page.tsx");
    expect(src).not.toMatch(/cookies\(\)/);
  });

  it("(marketing)/page.tsx source does not perform an authenticated fetch (no auth headers)", () => {
    const src = readSource("app/(marketing)/page.tsx");
    // No Authorization header usage
    expect(src).not.toMatch(/Authorization/);
    // No session cookie reference
    expect(src).not.toMatch(/ai_proxy_session/);
  });

  it("(marketing)/page.tsx is a Server Component (no 'use client' directive)", () => {
    const src = readSource("app/(marketing)/page.tsx");
    // Pure server component — no use client
    expect(src).not.toMatch(/"use client"/);
  });
});
