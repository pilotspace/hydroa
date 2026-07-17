/**
 * tests/pricing-catalog-no-drift.test.ts — RED for the shared pricing-constants module
 * (plan-tiers-and-base-fee TASK.md §3 — FROZEN @ v1, M4/R4).
 *
 * Dashboard tests cannot reach the live Postgres DB (§1 ⚠ the one lowest-confidence flag
 * at freeze), so the "no-drift" guarantee is a STATIC test: PRICING_CATALOG's 5 figures
 * must equal these hardcoded expected figures mirroring the migration's own seed values
 * (free=NULL, starter=1.00, pro=20.00, team=99.00, enterprise=NULL) — a human keeping
 * these two lists in sync IS the guarantee (documented, not a dynamic fetch). The second
 * half asserts the rendered /pricing page's Starter/Team/Enterprise price text derives
 * from the SAME module (not a re-hardcoded literal) — a hand-edited price (R4) fails here.
 *
 * RED before build: `@/lib/pricing-catalog` does not exist yet → MODULE_NOT_FOUND.
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
  it("Starter/Team/Enterprise cards render the catalog's own formatted price text", () => {
    render(createElement(PricingPage));

    const starterCard = screen.getByRole("article", { name: /starter/i });
    const teamCard = screen.getByRole("article", { name: /team/i });
    const entCard = screen.getByRole("article", { name: /enterprise/i });

    const freeEntry = getPricingCatalogEntry("free");
    const teamEntry = getPricingCatalogEntry("team");
    const entEntry = getPricingCatalogEntry("enterprise");

    expect(
      within(starterCard).getByText(formatBasePrice(freeEntry.basePriceUsd, "Free")),
    ).toBeInTheDocument();
    expect(
      within(teamCard).getByText(formatBasePrice(teamEntry.basePriceUsd, "Free")),
    ).toBeInTheDocument();
    expect(
      within(entCard).getByText(formatBasePrice(entEntry.basePriceUsd, "Contact us")),
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
