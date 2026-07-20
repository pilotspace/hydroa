/**
 * tests/pricing-tier-ladder.test.tsx — RED suite for `pricing-tier-ladder` TASK.md §3
 * (FROZEN @ v1 — a CHANGE REQUEST to `plan-tiers-and-base-fee` TASK.md §3's
 * "still 3 rendered cards" clause; every other clause there is untouched).
 *
 * Covers the §2 scenarios NOT already owned by `tests/pricing-catalog-no-drift.test.ts`
 * (M8, folded into that file additively) or by the FROZEN, unedited
 * `tests/pricing-page.test.tsx` (M9/R3/R4, confirmed compatible at 5 cards by static
 * read + a baseline green run — see TASK.md §4 evidence; NOT re-asserted here to avoid
 * duplicating an already-owned invariant).
 *
 * VACUOUS-PASS DISCIPLINE: several of these scenarios (M4 "Team stays sole featured",
 * M7 heading order, the dark-theme edge case) already hold true under TODAY's 3-card
 * page — so every test below is explicitly gated on a leading assertion that is FALSE
 * today (`cards).toHaveLength(5)`, a card queried by its NEW name, or a catalog-derived
 * price that does not match today's rendered text) so the whole test cannot pass until
 * the real 5-card implementation exists. One scenario — "an unknown catalog name still
 * throws defensively" — is a genuine REGRESSION PIN (already-shipped behavior in
 * lib/pricing-catalog.ts, unrelated to this task's own change) and is labelled as such,
 * excluded from the red count.
 *
 * Prices are NEVER hand-typed as a second literal — every expected price string is
 * computed via `formatBasePrice(getPricingCatalogEntry(name).basePriceUsd, nullLabel)`,
 * the same invariant `pricing-catalog-no-drift.test.ts` enforces.
 *
 * RED before Build: the page still renders 3 cards (Starter[bound to free]/Team/
 * Enterprise) — every assertion below that depends on 5 cards or the new Free/Starter/
 * Pro naming fails against the CURRENT page.tsx.
 */
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import {
  getPricingCatalogEntry,
  formatBasePrice,
} from "@/lib/pricing-catalog";
import PricingPage from "@/app/(marketing)/pricing/page";

const EXPECTED_ORDER = ["Free", "Starter", "Pro", "Team", "Enterprise"] as const;

function renderPricing() {
  const { container } = render(<PricingPage />);
  return container;
}

function priceOf(catalogName: string, nullLabel: string): string {
  return formatBasePrice(getPricingCatalogEntry(catalogName).basePriceUsd, nullLabel);
}

// ── M1 — all 5 tiers render in ascending order ──────────────────────────────
describe("test_all_5_tiers_render_in_ascending_order", () => {
  it("renders exactly 5 <article> cards, in order Free, Starter, Pro, Team, Enterprise", () => {
    renderPricing();
    const cards = screen.getAllByRole("article");
    expect(cards).toHaveLength(5); // fails today: current page renders 3

    const names = cards.map((card) => {
      const heading = within(card).getByRole("heading", { level: 2 });
      return heading.textContent?.trim() ?? "";
    });
    expect(names).toEqual([...EXPECTED_ORDER]);
  });

  it("every card's price text equals formatBasePrice(that tier's basePriceUsd, its nullLabel)", () => {
    renderPricing();
    const expectations: Array<[name: string, catalogKey: string, nullLabel: string]> = [
      ["Free", "free", "Free"],
      ["Starter", "starter", "Free"],
      ["Pro", "pro", "Free"],
      ["Team", "team", "Free"],
      ["Enterprise", "enterprise", "Contact us"],
    ];
    for (const [name, catalogKey, nullLabel] of expectations) {
      const card = screen.getByRole("article", { name: new RegExp(`^${name}$`, "i") });
      expect(within(card).getByText(priceOf(catalogKey, nullLabel))).toBeInTheDocument();
    }
  });
});

// ── M2 — naming collision resolved: Free and Starter are distinct cards ────
describe("test_naming_collision_resolved_free_and_starter_distinct", () => {
  it("the free-bound card is 'Free' and the starter-bound card is 'Starter' — each resolves to exactly one card", () => {
    renderPricing();
    // Each throws if zero OR more than one match — the ambiguity guard IS the test.
    const freeCard = screen.getByRole("heading", { name: /^free$/i });
    const starterCard = screen.getByRole("heading", { name: /^starter$/i });
    expect(freeCard).toBeInTheDocument();
    expect(starterCard).toBeInTheDocument();
    expect(freeCard).not.toBe(starterCard);
  });
});

describe("test_free_feature_bullet_matches_real_seat_cap", () => {
  it("Free's bullet reads 'up to 1 user', not the stale 'up to 3 users'", () => {
    renderPricing();
    const freeCard = screen.getByRole("article", { name: /^free$/i });
    expect(within(freeCard).getByText(/up to 1 user/i)).toBeInTheDocument();
    expect(within(freeCard).queryByText(/up to 3 users/i)).not.toBeInTheDocument();
  });
});

// ── M3 — CTA hrefs and copy match the existing pattern ──────────────────────
describe("test_cta_hrefs_and_copy_match_existing_pattern", () => {
  it("Free/Starter/Pro/Team CTAs read 'Get started' -> /signup; Enterprise reads 'Talk to us' -> /signup", () => {
    renderPricing();
    for (const name of ["Free", "Starter", "Pro", "Team"]) {
      const card = screen.getByRole("article", { name: new RegExp(`^${name}$`, "i") });
      const cta = within(card).getByRole("link", { name: /get started/i });
      expect(cta).toHaveAttribute("href", "/signup");
    }
    const entCard = screen.getByRole("article", { name: /^enterprise$/i });
    const entCta = within(entCard).getByRole("link", { name: /talk to us/i });
    expect(entCta).toHaveAttribute("href", "/signup");
  });
});

// ── M4 — Team remains the sole featured card ────────────────────────────────
describe("test_team_remains_sole_featured_card", () => {
  it("exactly one card (Team) carries the 'Most popular' badge, given the page renders 5 cards", () => {
    renderPricing();
    // Gate on the new 5-card shape first so this cannot vacuously pass against
    // today's already-featured-Team 3-card page.
    expect(screen.getAllByRole("article")).toHaveLength(5);

    const badges = screen.getAllByText(/most popular/i);
    expect(badges).toHaveLength(1);
    const teamCard = screen.getByRole("article", { name: /^team$/i });
    expect(within(teamCard).getByText(/most popular/i)).toBeInTheDocument();
  });
});

// ── M5 / R5 — the 5-card grid never overflows horizontally ─────────────────
describe("test_5_card_grid_never_overflows_horizontally", () => {
  it("the grid container uses grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 inside max-w-7xl", () => {
    const container = renderPricing();
    const cards = screen.getAllByRole("article");
    expect(cards).toHaveLength(5); // gates against the old 3-card max-w-6xl/lg:grid-cols-3 grid

    const grid = cards[0].closest('[class*="grid-cols-1"]');
    expect(grid).not.toBeNull();
    const cls = (grid as HTMLElement).className;
    expect(cls).toContain("grid-cols-1");
    expect(cls).toContain("sm:grid-cols-2");
    expect(cls).toContain("lg:grid-cols-3");
    expect(cls).toContain("xl:grid-cols-5");
    expect(cls).toContain("max-w-7xl");
    // Base breakpoint must stay single-column (no override below `sm`) — the
    // structural proxy for "no horizontal overflow at mobile width" this
    // codebase's jsdom test harness uses (no real layout engine to measure
    // scrollWidth/clientWidth against — see tests/billing-invoices.test.tsx's
    // own `toHaveClass("overflow-auto")` convention for the same reason).
    expect(container.querySelector(".grid")).not.toBeNull();
  });
});

// ── M6 — every visual value on the 2 NEW cards traces to an existing token ──
describe("test_new_cards_use_only_existing_tokens", () => {
  it("Starter and Pro cards use only existing Aurora/Airier tokens or shadcn/ui primitives, zero raw hex/inline style", () => {
    renderPricing();
    for (const [name, catalogKey] of [
      ["Starter", "starter"],
      ["Pro", "pro"],
    ] as const) {
      const card = screen.getByRole("article", { name: new RegExp(`^${name}$`, "i") });
      // Gate on catalog-derived price FIRST (fails today — neither card exists) —
      // only once this holds does the token/style audit below become meaningful,
      // never a vacuous pass against an unrelated, already-token-only 3-card page.
      expect(within(card).getByText(priceOf(catalogKey, "Free"))).toBeInTheDocument();

      expect(card.outerHTML).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
      expect(card.outerHTML).not.toMatch(/rgb\(|rgba\(/);
      expect(card.querySelectorAll("[style]").length).toBe(0);
    }
  });
});

// ── M7 — heading order and landmark structure hold at 5 cards ──────────────
describe("test_heading_order_and_landmarks_hold_at_5_cards", () => {
  it("exactly one h1, no skipped heading level, and every card is a labelled <article> with a reachable CTA", () => {
    const container = renderPricing();
    const cards = screen.getAllByRole("article");
    expect(cards).toHaveLength(5); // gate — same invariant already holds at 3 cards

    expect(container.querySelectorAll("h1")).toHaveLength(1);
    const levels = Array.from(container.querySelectorAll("h1,h2,h3,h4,h5,h6")).map(
      (h) => Number(h.tagName[1]),
    );
    for (let i = 1; i < levels.length; i++) {
      expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
    }

    for (const card of cards) {
      expect(card).toHaveAttribute("aria-labelledby");
      expect(within(card).getByRole("heading", { level: 2 })).toBeInTheDocument();
      expect(within(card).getAllByRole("link").length).toBeGreaterThanOrEqual(1);
    }
  });
});

// ── R2 — regression guard: two cards can never share a visible name ────────
describe("test_regression_guard_no_duplicate_visible_names", () => {
  it("each of the 5 tier names resolves to exactly one card via role+name, never throwing on ambiguity", () => {
    renderPricing();
    for (const name of EXPECTED_ORDER) {
      // getByRole throws both on zero AND on >1 match — the ambiguity guard IS
      // the assertion. Fails today for Free/Starter/Pro (zero match).
      const card = screen.getByRole("article", { name: new RegExp(`^${name}$`, "i") });
      expect(card).toBeInTheDocument();
    }
    const cards = screen.getAllByRole("article");
    const uniqueNames = new Set(
      cards.map((c) => within(c).getByRole("heading", { level: 2 }).textContent?.trim()),
    );
    expect(uniqueNames.size).toBe(5);
  });
});

// ── edge case — dark theme renders all 5 cards with correct contrast ───────
describe("test_dark_theme_renders_all_5_cards_correctly", () => {
  it("every card's price/background pairing uses only theme-aware token classes, never a raw color", () => {
    renderPricing();
    const expectations: Array<[name: string, catalogKey: string, nullLabel: string]> = [
      ["Free", "free", "Free"],
      ["Starter", "starter", "Free"],
      ["Pro", "pro", "Free"],
      ["Team", "team", "Free"],
      ["Enterprise", "enterprise", "Contact us"],
    ];
    // NOTE (method honesty): this is a STATIC/structural check — no globals.css
    // is loaded in this jsdom harness (confirmed: tests/setup.ts imports no
    // stylesheet) and no browser theme toggle is simulated, so this cannot
    // measure ACTUAL computed contrast in light vs dark. It instead pins that
    // every card's rendered markup uses only CSS-custom-property-backed token
    // classes (which the globals.css .dark block already overrides for every
    // token this page uses) rather than a raw/hardcoded color that would NOT
    // flip with the theme — the same structural proxy landing-page.test.tsx
    // and pricing-page.test.tsx's own axe checks rely on.
    for (const [name, catalogKey, nullLabel] of expectations) {
      const card = screen.getByRole("article", { name: new RegExp(`^${name}$`, "i") });
      expect(within(card).getByText(priceOf(catalogKey, nullLabel))).toBeInTheDocument();
      expect(card.outerHTML).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
      expect(card.querySelectorAll("[style]").length).toBe(0);
    }
  });
});

// ── edge case — an unknown catalog name still throws defensively ───────────
// REGRESSION PIN: getPricingCatalogEntry's throw-on-unknown-name guard is
// already-shipped behavior in lib/pricing-catalog.ts (read in full at TASK.md
// §0) — this task introduces zero new lookups and does not change this
// function. This test is GREEN today and stays green; it is a regression
// guard, not new coverage, and is excluded from this suite's red count.
describe("test_regression_pin_unknown_catalog_name_throws", () => {
  it("[REGRESSION PIN] getPricingCatalogEntry throws on an unknown tier name, never silently returns undefined", () => {
    expect(() => getPricingCatalogEntry("not-a-real-tier")).toThrow(
      /unknown tier "not-a-real-tier"/,
    );
  });
});

// ── edge case — keyboard-only navigation reaches every CTA in visual order ─
describe("test_keyboard_nav_reaches_every_cta_in_visual_order", () => {
  it("CTA links appear in DOM order Free -> Starter -> Pro -> Team -> Enterprise (the tab-order proxy this harness can assert)", () => {
    // NOTE (method honesty): DOM order is the proxy this suite (and the rest
    // of this codebase's jsdom harness — no real browser focus/Tab simulation
    // exists elsewhere either) uses for tab order, since the grid is a single
    // source-order flow with no explicit tabIndex/CSS-order override anywhere
    // on this page. It is not a literal simulated keyboard walk.
    const container = renderPricing();
    const cards = screen.getAllByRole("article");
    expect(cards).toHaveLength(5); // gate — fails today (3 cards)

    const ctaLabels = cards.map((card) => {
      const link = within(card).getAllByRole("link")[0];
      return link.textContent?.trim() ?? "";
    });
    expect(ctaLabels).toEqual([
      "Get started",
      "Get started",
      "Get started",
      "Get started",
      "Talk to us",
    ]);

    // And every CTA is a real focusable anchor (inherited Button asChild Link
    // primitive — no new interactive component type introduced).
    for (const card of cards) {
      const link = within(card).getAllByRole("link")[0];
      expect(link.tagName).toBe("A");
      expect(link).toHaveAttribute("href");
    }
    void container;
  });
});
