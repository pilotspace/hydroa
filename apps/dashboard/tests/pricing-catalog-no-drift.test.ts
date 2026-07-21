/**
 * tests/pricing-catalog-no-drift.test.ts — RED for the shared pricing-constants module
 * (plan-tiers-and-base-fee TASK.md §3 — FROZEN @ v1, M4/R4).
 *
 * Dashboard tests cannot reach the live Postgres DB (§1 ⚠ the one lowest-confidence flag
 * at freeze), so the "no-drift" guarantee is a STATIC test: PRICING_CATALOG's 5 figures
 * must equal these hardcoded expected figures mirroring the migration's own seed values
 * (free=NULL, starter=1.00, pro=20.00, team=99.00, enterprise=NULL) — a human keeping
 * these two lists in sync IS the guarantee (documented, not a dynamic fetch). The second
 * half asserts the rendered /pricing page's Free/Starter/Pro/Team/Enterprise price text
 * derives from the SAME module (not a re-hardcoded literal) — a hand-edited price (R4)
 * fails here.
 *
 * RED before build: `@/lib/pricing-catalog` does not exist yet → MODULE_NOT_FOUND.
 *
 * AMENDED by `pricing-tier-ladder` TASK.md §3 (CHANGE REQUEST to this file's own
 * "still 3 rendered cards" era — the underlying no-drift MECHANISM/invariant is
 * UNCHANGED, only the selector text + card count grow, additively, from 3 to 5, per
 * §3/M8): `test_pricing_page_derives_from_catalog_not_a_literal`'s 3 existing
 * assertions are RETARGETED to query by each tier's NEW visible name — the free-bound
 * card is now named "Free" (was mislabeled "Starter"; see pricing-tier-ladder TASK.md
 * §1 M2) — PLUS 2 NEW assertions for the catalog's own real `starter`($1)/`pro`($20)
 * entries, which the page did not render at all before this task. Nothing here is
 * weakened: every assertion still ties rendered price text to `formatBasePrice(...)`,
 * never a re-hardcoded literal.
 */
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { createElement } from "react";
import {
  PRICING_CATALOG,
  formatBasePrice,
  getPricingCatalogEntry,
} from "@/lib/pricing-catalog";
import PricingPage from "@/app/(marketing)/pricing/page";

// Mirrors the migration's own seed values EXACTLY (plan-tiers-and-base-fee TASK.md §3
// Schema) — the hand-authored half of the no-drift guarantee.
const EXPECTED_SEED_BASE_PRICES: Record<string, number | null> = {
  free: null,
  starter: 1.0,
  pro: 20.0,
  team: 99.0,
  enterprise: null,
};

describe("test_pricing_catalog_matches_seeded_backend", () => {
  it("PRICING_CATALOG holds exactly the 5 tiers with the seeded base_price_usd_monthly", () => {
    expect(PRICING_CATALOG).toHaveLength(5);
    const names = PRICING_CATALOG.map((t) => t.name).sort();
    expect(names).toEqual(["enterprise", "free", "pro", "starter", "team"]);
    for (const tier of PRICING_CATALOG) {
      expect(tier.basePriceUsd).toBe(EXPECTED_SEED_BASE_PRICES[tier.name]);
    }
  });
});

describe("test_pricing_page_derives_from_catalog_not_a_literal", () => {
  it("Free/Team/Enterprise cards render the catalog's own formatted price text", () => {
    render(createElement(PricingPage));

    // RETARGETED (pricing-tier-ladder §3 M8): the free-bound card's visible name
    // is now "Free" (was mislabeled "Starter") — same underlying invariant
    // (rendered price === formatBasePrice(catalog entry)), new selector text only.
    const freeCard = screen.getByRole("article", { name: /^free$/i });
    const teamCard = screen.getByRole("article", { name: /team/i });
    const entCard = screen.getByRole("article", { name: /enterprise/i });

    const freeEntry = getPricingCatalogEntry("free");
    const teamEntry = getPricingCatalogEntry("team");
    const entEntry = getPricingCatalogEntry("enterprise");

    // Free tier's null base price renders "$0" (Tin-decided 2026-07-21, re-cross) so it
    // does not duplicate the card's own "Free" title as an ambiguous by-text match.
    expect(
      within(freeCard).getByText(formatBasePrice(freeEntry.basePriceUsd, "$0")),
    ).toBeInTheDocument();
    expect(
      within(teamCard).getByText(formatBasePrice(teamEntry.basePriceUsd, "Free")),
    ).toBeInTheDocument();
    expect(
      within(entCard).getByText(formatBasePrice(entEntry.basePriceUsd, "Contact us")),
    ).toBeInTheDocument();
  });

  // NEW (pricing-tier-ladder §3 M8): the catalog's own real `starter`/`pro` entries
  // were never rendered anywhere before this task — this is genuinely new coverage,
  // not a retarget. Both prices are derived from PRICING_CATALOG, never hardcoded.
  it("Starter and Pro cards render the catalog's own formatted price text (NEW cards)", () => {
    render(createElement(PricingPage));

    const starterCard = screen.getByRole("article", { name: /^starter$/i });
    const proCard = screen.getByRole("article", { name: /^pro$/i });

    const starterEntry = getPricingCatalogEntry("starter");
    const proEntry = getPricingCatalogEntry("pro");

    expect(
      within(starterCard).getByText(formatBasePrice(starterEntry.basePriceUsd, "Free")),
    ).toBeInTheDocument();
    expect(
      within(proCard).getByText(formatBasePrice(proEntry.basePriceUsd, "Free")),
    ).toBeInTheDocument();
  });
});

describe("test_reject_hand_edited_price_would_fail_no_drift", () => {
  it("a tier's formatted price is a pure function of PRICING_CATALOG, not a re-hardcoded literal", () => {
    // A hand-edited page price (e.g. Team shown as "$199" without updating the module)
    // is caught because the page's rendered text is asserted to equal
    // formatBasePrice(catalog value) above — this test documents the invariant that
    // makes that drift structurally impossible to pass silently.
    const teamEntry = getPricingCatalogEntry("team");
    expect(formatBasePrice(teamEntry.basePriceUsd, "Free")).toBe("$99");
    expect(formatBasePrice(teamEntry.basePriceUsd, "Free")).not.toBe("$199");
  });
});
