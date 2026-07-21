/**
 * tests/pricing-cta-signup-entry.test.tsx — regression pin for `signup-refusal-router`
 * TASK.md §3 (FROZEN @ v1)'s "Least-sure flag surfaced at freeze":
 *   "[contract] the /pricing CTAs (href: "/signup", both cards) are now in
 *   scope per the human decision — they are a SECOND entry point into the
 *   same wall, and the contract must cover them, not just the homepage."
 *
 * HONEST NOTE (not silently omitted): unlike every other test in this suite,
 * this one is ALREADY GREEN today, before Build. Reading
 * app/(marketing)/pricing/page.tsx directly (Ground SHA 8daf22c) confirms the
 * "Starter" and "Team" tier cards (both labeled "Get started") already point
 * `cta.href` at "/signup" — the fix this task ships (the M1 routing panel)
 * lands on THAT SAME route, so this second entry point inherits the fix
 * transitively with zero pricing-page code change. This test exists as a
 * REGRESSION PIN so a future pricing redesign cannot silently re-break this
 * entry point, not as evidence of new Build work — it is intentionally NOT
 * counted toward this suite's red-count (see TASK.md §4).
 *
 * "Enterprise" (labeled "Talk to us") is deliberately excluded — its distinct
 * copy signals a different intent (a sales conversation, not self-serve
 * signup) even though its href happens to match today; asserting on it here
 * would over-constrain a card this task's contract note did not name.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

async function renderPricing() {
  const mod = await import("@/app/(marketing)/pricing/page");
  const Page = mod.default;
  return render(<Page />);
}

describe("Pricing page — /signup as the second entry point (contract note)", () => {
  it("test_pricing_cta_starter_and_team_point_to_signup", async () => {
    await renderPricing();

    const links = screen.getAllByRole("link", { name: /get started/i });
    expect(links.length).toBeGreaterThanOrEqual(2);
    for (const link of links) {
      expect(link).toHaveAttribute("href", "/signup");
    }
  });
});
