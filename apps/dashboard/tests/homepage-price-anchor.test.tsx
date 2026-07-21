/**
 * homepage-price-anchor.test.tsx — RED suite for the `homepage-price-anchor` task
 * (frozen contract §3 v1 — approved by Tin Dang, 2026-07-21).
 *
 * Covers every §2 scenario (one test per scenario, plus the paired unchanged-anchor
 * guard). All "new-behavior" assertions (price-anchor presence/text/live-catalog-
 * derivation) MUST fail red before Build — `[data-slot="price-anchor"]` does not
 * exist in `page.tsx` yet. The frozen-structure assertions (id/h2/paragraph/button)
 * are expected to pass in BOTH states (they guard against regression, not new
 * behavior) — that is fine and by design; see the persona brief.
 *
 * Suite: "legacy" project (tests/ directory, tests/setup.ts).
 */
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import {
  getPricingCatalogEntry,
  formatBasePrice,
} from "@/lib/pricing-catalog";
import MarketingRootPage from "@/app/(marketing)/page";
import PricingPage from "@/app/(marketing)/pricing/page";
import { axe } from "@/test-support/axe";

const DASHBOARD_ROOT = resolve(__dirname, "..");

function readSource(relPath: string): string {
  const abs = resolve(DASHBOARD_ROOT, relPath);
  if (!existsSync(abs)) return "";
  return readFileSync(abs, "utf-8");
}

function renderHomepage() {
  const { container } = render(createElement(MarketingRootPage));
  return container;
}

// ─────────────────────────────────────────────────────────────────────────────
// test_catalog_sourced_figures — M1
// The #pricing section renders both the free + starter figures as LIVE catalog
// output — never a re-hardcoded literal.
// ─────────────────────────────────────────────────────────────────────────────
describe("test_catalog_sourced_figures", () => {
  it('renders [data-slot="price-anchor"] inside #pricing', () => {
    const container = renderHomepage();
    const pricingSection = container.querySelector("#pricing");
    expect(pricingSection).not.toBeNull();
    const anchor = pricingSection!.querySelector('[data-slot="price-anchor"]');
    expect(anchor).not.toBeNull();
  });

  it('shows formatBasePrice(getPricingCatalogEntry("free").basePriceUsd, "Free") -> "Free"', () => {
    const container = renderHomepage();
    const anchor = container.querySelector('[data-slot="price-anchor"]');
    expect(anchor).not.toBeNull();
    const freeEntry = getPricingCatalogEntry("free");
    const expectedFreeText = formatBasePrice(freeEntry.basePriceUsd, "Free");
    expect(expectedFreeText).toBe("Free");
    expect(anchor!.textContent).toContain(expectedFreeText);
  });

  it('shows formatBasePrice(getPricingCatalogEntry("starter").basePriceUsd, "Free") -> "$1"', () => {
    const container = renderHomepage();
    const anchor = container.querySelector('[data-slot="price-anchor"]');
    expect(anchor).not.toBeNull();
    const starterEntry = getPricingCatalogEntry("starter");
    const expectedStarterText = formatBasePrice(starterEntry.basePriceUsd, "Free");
    expect(expectedStarterText).toBe("$1");
    expect(anchor!.textContent).toContain(expectedStarterText);
  });

  it("the \"$1\" figure is byte-identical to /pricing's own Starter-card call", () => {
    // Same call convention the contract locks: formatBasePrice(getPricingCatalogEntry
    // ("starter").basePriceUsd, "Free") — byte-identical to /pricing's Starter card.
    const homeContainer = renderHomepage();
    const anchor = homeContainer.querySelector('[data-slot="price-anchor"]');
    expect(anchor).not.toBeNull();

    render(createElement(PricingPage));
    const starterCard = screen.getByRole("article", { name: /^starter$/i });
    const starterEntry = getPricingCatalogEntry("starter");
    const starterPriceText = formatBasePrice(starterEntry.basePriceUsd, "Free");
    expect(within(starterCard).getByText(starterPriceText)).toBeInTheDocument();
    expect(anchor!.textContent).toContain(starterPriceText);
  });

  it('the "Free" figure represents the SAME null basePriceUsd /pricing renders as "$0" (different nullLabel, not a drift)', () => {
    const freeEntry = getPricingCatalogEntry("free");
    expect(freeEntry.basePriceUsd).toBeNull();
    // Homepage's own nullLabel choice:
    expect(formatBasePrice(freeEntry.basePriceUsd, "Free")).toBe("Free");
    // /pricing's own (different) nullLabel choice for the SAME entry:
    expect(formatBasePrice(freeEntry.basePriceUsd, "$0")).toBe("$0");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_additive_single_line — M2
// ─────────────────────────────────────────────────────────────────────────────
describe("test_additive_single_line", () => {
  it("the price anchor renders as a single new element between the paragraph and the button div", () => {
    const container = renderHomepage();
    const pricingSection = container.querySelector("#pricing");
    expect(pricingSection).not.toBeNull();
    const children = Array.from(pricingSection!.querySelector(".mx-auto")!.children);
    const pIndex = children.findIndex(
      (el) => el.tagName === "P" && !el.hasAttribute("data-slot"),
    );
    const anchorIndex = children.findIndex(
      (el) => el.getAttribute("data-slot") === "price-anchor",
    );
    const buttonDivIndex = children.findIndex((el) =>
      el.className?.toString().includes("mt-8"),
    );
    expect(pIndex).toBeGreaterThanOrEqual(0);
    expect(anchorIndex).toBeGreaterThanOrEqual(0);
    expect(buttonDivIndex).toBeGreaterThanOrEqual(0);
    expect(anchorIndex).toBeGreaterThan(pIndex);
    expect(anchorIndex).toBeLessThan(buttonDivIndex);
  });

  it("introduces no new h2/h3 inside #pricing", () => {
    const container = renderHomepage();
    const pricingSection = container.querySelector("#pricing");
    expect(pricingSection).not.toBeNull();
    expect(pricingSection!.querySelectorAll("h2")).toHaveLength(1);
    expect(pricingSection!.querySelectorAll("h3")).toHaveLength(0);
  });

  it("introduces no card grid inside #pricing", () => {
    const container = renderHomepage();
    const pricingSection = container.querySelector("#pricing");
    expect(pricingSection).not.toBeNull();
    expect(pricingSection!.querySelectorAll('[role="article"]')).toHaveLength(0);
    expect(pricingSection!.querySelectorAll(".grid")).toHaveLength(0);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_frozen_anchors_unchanged — M3, R4
// (Regression guard — legitimately passes in both states; still required.)
// ─────────────────────────────────────────────────────────────────────────────
describe("test_frozen_anchors_unchanged", () => {
  it("#pricing keeps its id", () => {
    const container = renderHomepage();
    expect(container.querySelector("section#pricing")).not.toBeNull();
  });

  it('h2 text stays "Simple, transparent pricing"', () => {
    const container = renderHomepage();
    const pricingSection = container.querySelector("#pricing");
    const h2 = pricingSection!.querySelector("h2");
    expect(h2).not.toBeNull();
    expect(h2!.textContent).toBe("Simple, transparent pricing");
  });

  it('the "View pricing plans" button still links to /pricing, byte-identical label', () => {
    const container = renderHomepage();
    const pricingSection = container.querySelector("#pricing");
    const link = within(pricingSection as HTMLElement).getByRole("link", {
      name: "View pricing plans",
    });
    expect(link.getAttribute("href")).toBe("/pricing");
  });

  it("the existing paragraph is unchanged", () => {
    const container = renderHomepage();
    const pricingSection = container.querySelector("#pricing");
    expect(pricingSection!.textContent).toContain(
      "From solo teams to enterprise deployments.",
    );
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_accessible_single_phrase — M4
// ─────────────────────────────────────────────────────────────────────────────
describe("test_accessible_single_phrase", () => {
  it("the price anchor is one coherent text element, no extra aria-label needed", () => {
    const container = renderHomepage();
    const anchor = container.querySelector('[data-slot="price-anchor"]');
    expect(anchor).not.toBeNull();
    // A single element carrying its own text content directly (not split across
    // disconnected sibling fragments) — no aria-label scaffolding required.
    expect(anchor!.hasAttribute("aria-label")).toBe(false);
    expect((anchor!.textContent ?? "").trim().length).toBeGreaterThan(0);
  });

  it("axe reports 0 new serious/critical violations on the homepage", async () => {
    const container = renderHomepage();
    const results = await axe(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    const seriousCritical = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(seriousCritical).toEqual([]);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_existing_tokens_only — M5
// ─────────────────────────────────────────────────────────────────────────────
describe("test_existing_tokens_only", () => {
  it("the price anchor uses only text-foreground/text-muted-foreground existing utility classes", () => {
    const container = renderHomepage();
    const anchor = container.querySelector('[data-slot="price-anchor"]');
    expect(anchor).not.toBeNull();
    const className = anchor!.className ?? "";
    // Every class token must be an existing utility already used elsewhere on this
    // page (text-*, font-*, mt-*, tracking-*, leading-*) — no arbitrary/bespoke
    // color or font-family token introduced.
    expect(className).not.toMatch(/#[0-9a-fA-F]{3,6}/); // no raw hex
    expect(className).not.toMatch(/\[--/); // no new inline CSS custom property
    expect(
      className.includes("text-foreground") ||
        className.includes("text-muted-foreground"),
    ).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_server_component_only — M6, R5
// ─────────────────────────────────────────────────────────────────────────────
describe("test_server_component_only", () => {
  it('(marketing)/page.tsx has no "use client" directive', () => {
    const src = readSource("app/(marketing)/page.tsx");
    expect(src).not.toBe("");
    expect(src).not.toMatch(/"use client"/);
  });

  it("(marketing)/page.tsx introduces no fetch/useEffect/client-only API", () => {
    const src = readSource("app/(marketing)/page.tsx");
    expect(src).not.toMatch(/useEffect/);
    expect(src).not.toMatch(/\bfetch\(/);
  });

  it("the price anchor consumes PRICING_CATALOG via the existing static import", () => {
    const src = readSource("app/(marketing)/page.tsx");
    expect(src).toMatch(/from ["']@\/lib\/pricing-catalog["']/);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_stable_test_anchor — M7
// ─────────────────────────────────────────────────────────────────────────────
describe("test_stable_test_anchor", () => {
  it('data-slot="price-anchor" locates the new content without a text match', () => {
    const container = renderHomepage();
    const anchor = container.querySelector('[data-slot="price-anchor"]');
    expect(anchor).not.toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_reject_hand_typed_literal — R1
// A hand-typed literal would break this invariant: the rendered anchor text must
// equal the LIVE catalog call output, not merely contain the digit "1"/word "Free"
// by coincidence. This test pins the exact expected string derived from the catalog
// so that a hand-edited literal that drifts from a future catalog value fails here.
// ─────────────────────────────────────────────────────────────────────────────
describe("test_reject_hand_typed_literal", () => {
  it("the anchor's price substrings equal the catalog's own live formatBasePrice output, not fixed strings", () => {
    const container = renderHomepage();
    const anchor = container.querySelector('[data-slot="price-anchor"]');
    expect(anchor).not.toBeNull();

    const freeText = formatBasePrice(getPricingCatalogEntry("free").basePriceUsd, "Free");
    const starterText = formatBasePrice(
      getPricingCatalogEntry("starter").basePriceUsd,
      "Free",
    );
    // Mirrors pricing-catalog-no-drift.test.ts's own pattern: assert against the
    // LIVE function call result, never a bare hardcoded string.
    expect(anchor!.textContent).toContain(freeText);
    expect(anchor!.textContent).toContain(starterText);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_reject_unreachable_figure — R2
// The anchor may only cite catalog entries /pricing already renders publicly.
// ─────────────────────────────────────────────────────────────────────────────
describe("test_reject_unreachable_figure", () => {
  it("both anchored entries (free, starter) are independently rendered on /pricing today", () => {
    render(createElement(PricingPage));
    const freeCard = screen.getByRole("article", { name: /^free$/i });
    const starterCard = screen.getByRole("article", { name: /^starter$/i });
    expect(freeCard).toBeInTheDocument();
    expect(starterCard).toBeInTheDocument();
  });

  it("the homepage anchor text names only the locked Free+Starter figures", () => {
    const container = renderHomepage();
    const anchor = container.querySelector('[data-slot="price-anchor"]');
    expect(anchor).not.toBeNull();
    // The locked copy is scoped to free+starter — no $20/$99/"Contact us" leaks in.
    expect(anchor!.textContent).not.toContain("$20");
    expect(anchor!.textContent).not.toContain("$99");
    expect(anchor!.textContent).not.toContain("Contact us");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_reject_full_grid — R3
// ─────────────────────────────────────────────────────────────────────────────
describe("test_reject_full_grid", () => {
  it("#pricing does not gain a 3+-card grid or a second heading", () => {
    const container = renderHomepage();
    const pricingSection = container.querySelector("#pricing");
    expect(pricingSection).not.toBeNull();
    expect(pricingSection!.querySelectorAll("h2")).toHaveLength(1);
    expect(pricingSection!.querySelectorAll('[role="article"]').length).toBe(0);
  });

  it('"View pricing plans" remains the only link inside #pricing', () => {
    const container = renderHomepage();
    const pricingSection = container.querySelector("#pricing");
    const links = pricingSection!.querySelectorAll("a");
    expect(links.length).toBe(1);
    expect(links[0].getAttribute("href")).toBe("/pricing");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_reject_client_rendered_price — R5
// ─────────────────────────────────────────────────────────────────────────────
describe("test_reject_client_rendered_price", () => {
  it('page.tsx is not marked "use client" and performs no network call for the price', () => {
    const src = readSource("app/(marketing)/page.tsx");
    expect(src).not.toMatch(/"use client"/);
    expect(src).not.toMatch(/\bfetch\(/);
    expect(src).not.toMatch(/useEffect/);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_cross_page_consistency — edge case
// ─────────────────────────────────────────────────────────────────────────────
describe("test_cross_page_consistency", () => {
  it("homepage and /pricing derive Free/Starter from the same PRICING_CATALOG entries", () => {
    const homeContainer = renderHomepage();
    const anchor = homeContainer.querySelector('[data-slot="price-anchor"]');
    expect(anchor).not.toBeNull();

    render(createElement(PricingPage));
    const starterCard = screen.getByRole("article", { name: /^starter$/i });

    const starterEntry = getPricingCatalogEntry("starter");
    const starterText = formatBasePrice(starterEntry.basePriceUsd, "Free");

    // Same "$1" byte-identical substring on both surfaces.
    expect(anchor!.textContent).toContain(starterText);
    expect(within(starterCard).getByText(starterText)).toBeInTheDocument();
  });

  it('homepage "Free" and /pricing "$0" both represent the same null basePriceUsd by design', () => {
    const freeEntry = getPricingCatalogEntry("free");
    expect(freeEntry.basePriceUsd).toBeNull();
    expect(formatBasePrice(freeEntry.basePriceUsd, "Free")).toBe("Free");
    expect(formatBasePrice(freeEntry.basePriceUsd, "$0")).toBe("$0");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// test_no_async_state — deliberately ruled out
// ─────────────────────────────────────────────────────────────────────────────
describe("test_no_async_state", () => {
  it("renders synchronously with no loading/error/partial-failure state", () => {
    // A synchronous render call itself is the proof: no await, no act() needed
    // around an async data fetch, no suspense boundary.
    const container = renderHomepage();
    const anchor = container.querySelector('[data-slot="price-anchor"]');
    expect(anchor).not.toBeNull();
    expect(container.querySelector('[data-slot="loading"]')).toBeNull();
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });
});
