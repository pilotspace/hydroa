/**
 * lib/pricing-catalog.ts — shared pricing-constants module (plan-tiers-and-base-fee
 * TASK.md §3 — FROZEN @ v1, M4).
 *
 * Dashboard tests cannot reach the live Postgres DB, so this module IS the no-drift
 * binding mechanism (§1 ⚠ the one lowest-confidence flag at freeze): a static,
 * TEST-enforced source of truth the /pricing page renders FROM, mirroring the backend
 * migration's own seed values EXACTLY (apps/gateway/migrations/versions/
 * 113ebdbe9f09_plan_tiers_and_base_fee.py) — a human keeping these two lists in sync IS
 * the guarantee, not a dynamic fetch (Tin-locked decision).
 *
 * `basePriceUsd: null` means "no flat base fee" (free/enterprise) — the CALLER decides
 * how to render that (e.g. "Free" for the free-tier card, "Contact us" for Enterprise);
 * this module never hardcodes which null-label applies to which tier.
 */

export type PricingCatalogEntry = {
  name: string;
  displayName: string;
  basePriceUsd: number | null;
};

// Mirrors the migration's Final catalog table EXACTLY (§3 CONTRACT):
//   free       | Free       | base_price=NULL
//   starter    | Starter    | base_price=1.00
//   pro        | Pro        | base_price=20.00
//   team       | Team       | base_price=99.00
//   enterprise | Enterprise | base_price=NULL
export const PRICING_CATALOG: PricingCatalogEntry[] = [
  { name: "free", displayName: "Free", basePriceUsd: null },
  { name: "starter", displayName: "Starter", basePriceUsd: 1.0 },
  { name: "pro", displayName: "Pro", basePriceUsd: 20.0 },
  { name: "team", displayName: "Team", basePriceUsd: 99.0 },
  { name: "enterprise", displayName: "Enterprise", basePriceUsd: null },
];

export function getPricingCatalogEntry(name: string): PricingCatalogEntry {
  const entry = PRICING_CATALOG.find((tier) => tier.name === name);
  if (!entry) {
    throw new Error(`pricing-catalog: unknown tier "${name}"`);
  }
  return entry;
}

/**
 * Formats a base price for display — `null` renders as `nullLabel` (the caller's own
 * copy, e.g. "Free" for the free tier's card, "Contact us" for Enterprise's — this
 * module never hardcodes which applies to which tier); a non-null value renders as a
 * whole-dollar `$N` (every seeded figure is a whole dollar amount today).
 */
export function formatBasePrice(basePriceUsd: number | null, nullLabel: string): string {
  return basePriceUsd === null ? nullLabel : `$${basePriceUsd}`;
}
