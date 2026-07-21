/**
 * tests/base-url-swap.test.tsx — RED suite for `homepage-integration-proof`
 * TASK.md §3 (FROZEN @ v1): the one-line `base_url` swap proof mounted as the
 * last child of the homepage hero.
 *
 * Covers every §2 scenario (M1-M8, R1-R6). RED before Build:
 *   - `@/components/marketing/base-url-swap` does not exist yet
 *     (components/marketing/base-url-swap.tsx is a NEW FILE per §3 CONTRACT).
 *   - `(marketing)/page.tsx` does not yet mount it inside the hero.
 * Every scenario below therefore FIRST asserts the subject EXISTS via a
 * THROWING query (`getByTestId`/`getByRole`, never `queryBy*`) before auditing
 * it further — the VACUOUS-PASS RULE this milestone standardized on, so no
 * test here can pass merely because the element under test is absent.
 *
 * Stable lookup hooks this suite requires of Build (not new behavior — a
 * selector aid, mirroring the shipped `data-testid="quickstart-panel"`
 * precedent in components/keys/QuickstartPanel.tsx):
 *   - `data-testid="base-url-swap"` on BaseUrlSwap's root element
 *   - `data-testid="added-line"` on the ONE "+"-marked line
 *     (`base_url="{displayBaseUrl}/v1",`) — the line §3 requires to carry
 *     `text-success-text` distinctly from every other line's `text-foreground`
 *
 * Price/URL/key values are never hand-typed as a second, independent literal
 * where the contract already names an exact source (`publicApiBaseUrl()`, the
 * shared `PLACEHOLDER_BASE_URL`/`PLACEHOLDER_KEY` strings, `scripts/edge_smoke.sh`)
 * — each is read from that source at test time so a drift in the source shows
 * up as a failing assertion, not a silently-stale test.
 */
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { axe } from "@/test-support/axe";
import MarketingRootPage from "@/app/(marketing)/page";

const DASHBOARD_ROOT = resolve(__dirname, "..");
const PROJECT_ROOT = resolve(DASHBOARD_ROOT, "..", "..");

function readSource(rel: string): string {
  const abs = resolve(DASHBOARD_ROOT, rel);
  return existsSync(abs) ? readFileSync(abs, "utf-8") : "";
}
function readProjectFile(rel: string): string {
  const abs = resolve(PROJECT_ROOT, rel);
  return existsSync(abs) ? readFileSync(abs, "utf-8") : "";
}

// Ground truth this task must not diverge from (scripts/edge_smoke.sh, steps 5-6).
const EDGE_SMOKE_SOURCE = readProjectFile("scripts/edge_smoke.sh");

// Byte-identical to QuickstartPanel.tsx's own module-private literal (duplicated
// here per §3 CONTRACT — that constant is not exported).
const SHARED_PLACEHOLDER_BASE_URL = "<configure NEXT_PUBLIC_API_BASE_URL>";
// Byte-identical to docs/quickstart/page.tsx's PLACEHOLDER_KEY.
const SHARED_PLACEHOLDER_KEY = "sk-your-api-key";

const ORIGINAL_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL;
afterEach(() => {
  if (ORIGINAL_BASE_URL === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL;
  else process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_BASE_URL;
});

function renderHomepage() {
  const { container } = render(<MarketingRootPage />);
  return container;
}

// Ground-truth sanity: fails loudly (not silently) if edge_smoke.sh itself ever
// moves away from the shape this whole suite is anchored to — read once, asserted
// here so a drift in the ground-truth script is caught, not assumed.
describe("ground truth — scripts/edge_smoke.sh still verifies the shape this suite pins", () => {
  it("edge_smoke.sh's real call still uses /v1/chat/completions + Bearer + an sk- key", () => {
    expect(EDGE_SMOKE_SOURCE).not.toBe("");
    expect(EDGE_SMOKE_SOURCE).toMatch(/\/v1\/chat\/completions/);
    expect(EDGE_SMOKE_SOURCE).toMatch(/Authorization: Bearer \$(KEY|TOKEN)/);
  });
});

// ── M1 — Hero shows the one-line base_url diff ─────────────────────────────
describe("test_hero_shows_base_url_diff", () => {
  it("mounts BaseUrlSwap as the last element of the hero, after the CTA row, before #product", () => {
    const container = renderHomepage();
    const heroSection = container.querySelector('[aria-labelledby="hero-heading"]');
    expect(heroSection).not.toBeNull();
    const productSection = container.querySelector("#product");
    expect(productSection).not.toBeNull();

    // THROWING existence check — fails now because BaseUrlSwap is not mounted.
    const swap = within(heroSection as HTMLElement).getByTestId("base-url-swap");

    // Must live inside the hero, not inside #product.
    expect((heroSection as HTMLElement).contains(swap)).toBe(true);
    expect((productSection as HTMLElement).contains(swap)).toBe(false);

    // Must come after both hero CTAs (Get started / Log in) in document order.
    const getStarted = within(heroSection as HTMLElement).getByRole("link", { name: /get started/i });
    const logIn = within(heroSection as HTMLElement).getByRole("link", { name: /log in/i });
    expect(getStarted.compareDocumentPosition(swap) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(logIn.compareDocumentPosition(swap) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows exactly one line marked as added: base_url=\"{displayBaseUrl}/v1\",", () => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.hydroa.dev";
    const container = renderHomepage();
    const swap = within(container as unknown as HTMLElement).getByTestId("base-url-swap");
    const addedLine = within(swap).getByTestId("added-line");
    expect(addedLine.textContent ?? "").toMatch(/base_url="https:\/\/api\.hydroa\.dev\/v1",?/);
    // Exactly one added-line marker in the sample.
    expect(within(swap).getAllByTestId("added-line")).toHaveLength(1);
  });
});

// ── M4 — Call shape matches the verified real gateway surface ──────────────
describe("test_call_shape_matches_edge_smoke", () => {
  it("the added base_url line ends in /v1 and the key is sk--prefixed, matching edge_smoke.sh's real call", () => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.hydroa.dev";
    const container = renderHomepage();
    const swap = within(container as unknown as HTMLElement).getByTestId("base-url-swap");

    const addedLine = within(swap).getByTestId("added-line").textContent ?? "";
    // Path convention: edge_smoke.sh calls "$EDGE/v1/chat/completions" — the SDK's
    // base_url must end in exactly "/v1" (the SDK appends "/chat/completions").
    expect(addedLine).toMatch(/\/v1"/);
    expect(addedLine).not.toMatch(/\/v1\/"/); // no trailing double-slash divergence

    // Key format: edge_smoke.sh's real virtual key is sk--prefixed (confirmed via
    // the gateway docstring grep cited in TASK.md §0); the sample's placeholder key
    // must be the same "sk-" shape, never a differently-shaped fake key.
    expect(within(swap).getByText(new RegExp(SHARED_PLACEHOLDER_KEY.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")))).toBeInTheDocument();
    expect(SHARED_PLACEHOLDER_KEY).toMatch(/^sk-/);
  });
});

// ── M2 — Configured / unconfigured base URL ─────────────────────────────────
describe("test_configured_base_url_renders_verbatim", () => {
  it("renders the real NEXT_PUBLIC_API_BASE_URL origin + /v1, sourced from publicApiBaseUrl()", () => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://distinct-marker.example.com";
    const container = renderHomepage();
    const swap = within(container as unknown as HTMLElement).getByTestId("base-url-swap");
    const addedLine = within(swap).getByTestId("added-line").textContent ?? "";
    expect(addedLine).toMatch(/distinct-marker\.example\.com\/v1/);
  });
});

describe("test_unconfigured_base_url_falls_back_to_shared_placeholder", () => {
  it("renders the SAME placeholder QuickstartPanel already shows, never a fabricated domain", () => {
    delete process.env.NEXT_PUBLIC_API_BASE_URL;
    const container = renderHomepage();
    const swap = within(container as unknown as HTMLElement).getByTestId("base-url-swap");
    const addedLine = within(swap).getByTestId("added-line").textContent ?? "";
    expect(addedLine).toContain(SHARED_PLACEHOLDER_BASE_URL);
    expect(addedLine).not.toMatch(/https?:\/\//); // never a fabricated-looking URL
  });
});

// ── M3 — API key placeholder is safe and consistent ─────────────────────────
describe("test_api_key_placeholder_safe_and_consistent", () => {
  it("shows the literal sk-your-api-key, byte-identical to /docs/quickstart's PLACEHOLDER_KEY", () => {
    const container = renderHomepage();
    const swap = within(container as unknown as HTMLElement).getByTestId("base-url-swap");
    expect(within(swap).getByText(new RegExp(`api_key="${SHARED_PLACEHOLDER_KEY}"`))).toBeInTheDocument();

    const docsQuickstartSrc = readSource("app/(marketing)/docs/quickstart/page.tsx");
    expect(docsQuickstartSrc).toMatch(/const PLACEHOLDER_KEY = "sk-your-api-key";/);
  });
});

// ── M5 / R5 — No new client boundary ────────────────────────────────────────
describe("test_stays_inside_frozen_server_component_hero", () => {
  it("components/marketing/base-url-swap.tsx has no 'use client' directive and no browser-only API", () => {
    const src = readSource("components/marketing/base-url-swap.tsx");
    // NEW-RED: this file does not exist yet.
    expect(src).not.toBe("");
    expect(src).not.toMatch(/"use client"/);
    expect(src).not.toMatch(/\bwindow\.|\bdocument\.|\bnavigator\./);
  });

  // REGRESSION PIN — already true today (page.tsx predates this task and is
  // already a pure Server Component); this assertion does not depend on any
  // new implementation and is expected to stay green throughout. Listed here
  // as an explicit guard, not counted as new red coverage (see TASK.md §4).
  it("[REGRESSION PIN] (marketing)/page.tsx itself stays a Server Component (no 'use client')", () => {
    const src = readSource("app/(marketing)/page.tsx");
    expect(src).not.toBe("");
    expect(src).not.toMatch(/"use client"/);
  });
});

// ── M6 — Heading structure untouched ────────────────────────────────────────
describe("test_heading_structure_untouched", () => {
  it("BaseUrlSwap introduces no h1/h2/h3/h4/h5/h6 and the page keeps exactly one h1", () => {
    const container = renderHomepage();
    // THROWING existence check first — this is what keeps this test from
    // vacuously passing (the "exactly one h1" half is already true today).
    const swap = within(container as unknown as HTMLElement).getByTestId("base-url-swap");
    expect(within(swap).queryAllByRole("heading")).toHaveLength(0);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
  });
});

// ── M7 / R3 — No horizontal scroll at mobile width ──────────────────────────
describe("test_no_horizontal_scroll_at_mobile_width", () => {
  it("the code sample sits in its own overflow-x-auto container, same class set as QuickstartPanel's <pre>", () => {
    const container = renderHomepage();
    const swap = within(container as unknown as HTMLElement).getByTestId("base-url-swap");
    const pre = swap.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre).toHaveClass("overflow-x-auto");
    expect(pre).toHaveClass("rounded-md");
    expect(pre).toHaveClass("border-border");
    expect(pre).toHaveClass("bg-muted");
    // No fixed-pixel or arbitrary width utility that could defeat the scroll container.
    expect(pre?.className ?? "").not.toMatch(/\bw-\[|min-w-\[/);
  });
});

// ── M8 — Legible in both themes using existing tokens ───────────────────────
describe("test_legible_in_both_themes_via_existing_tokens", () => {
  it("every color used resolves to an existing token class; the added line alone carries text-success-text", () => {
    const container = renderHomepage();
    const swap = within(container as unknown as HTMLElement).getByTestId("base-url-swap");

    const eyebrow = within(swap).getByText(/drop-in proof/i);
    expect(eyebrow).toHaveClass("bg-accent-soft");
    expect(eyebrow).toHaveClass("text-accent-soft-foreground");

    const addedLine = within(swap).getByTestId("added-line");
    expect(addedLine).toHaveClass("text-success-text");

    const pre = swap.querySelector("pre");
    expect(pre).toHaveClass("text-foreground");

    // No raw hex literal or inline style anywhere in the sample.
    expect(swap.outerHTML).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(swap.querySelectorAll("[style]").length).toBe(0);
  });

  it("axe reports 0 serious/critical violations on the mounted sample", async () => {
    const container = renderHomepage();
    const swap = within(container as unknown as HTMLElement).getByTestId("base-url-swap");
    const results = await axe(swap);
    const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(serious).toEqual([]);
  });
});

// ── R1 — Reject: fabricated endpoint ────────────────────────────────────────
describe("test_reject_fabricated_endpoint", () => {
  it("the added line is always a pure function of publicApiBaseUrl(), never a literal domain", () => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://reject-probe.example.net";
    const container = renderHomepage();
    const swap = within(container as unknown as HTMLElement).getByTestId("base-url-swap");
    const addedLine = within(swap).getByTestId("added-line").textContent ?? "";
    // If the value were hardcoded, changing the env var would NOT change the
    // rendered output — this assertion fails exactly under that hand-edit.
    expect(addedLine).toContain("reject-probe.example.net");
  });
});

// ── R2 — Reject: heading order violation ────────────────────────────────────
describe("test_reject_heading_order_violation", () => {
  it("no second h1, no introduced h2/h3, no skipped level anywhere on the page after the change", () => {
    const container = renderHomepage();
    const swap = within(container as unknown as HTMLElement).getByTestId("base-url-swap"); // throws if absent
    expect(swap).toBeInTheDocument();
    const levels = Array.from(container.querySelectorAll("h1,h2,h3,h4,h5,h6")).map((h) => Number(h.tagName[1]));
    for (let i = 1; i < levels.length; i++) {
      expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
    }
    expect(container.querySelectorAll("h1")).toHaveLength(1);
  });
});

// ── R4 — Reject: unverified snippet shape ───────────────────────────────────
describe("test_reject_unverified_snippet", () => {
  it("the header/path/key-prefix shape never diverges from edge_smoke.sh's verified real call", () => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.hydroa.dev";
    const container = renderHomepage();
    const swap = within(container as unknown as HTMLElement).getByTestId("base-url-swap");
    const addedLine = within(swap).getByTestId("added-line").textContent ?? "";

    // Ground truth (edge_smoke.sh) — path is "/v1/chat/completions" off $EDGE.
    expect(EDGE_SMOKE_SOURCE).toMatch(/\/v1\/chat\/completions/);
    // Sample's base_url must end in "/v1" (the SDK appends "/chat/completions" itself)
    // — never a different path shape (e.g. "/api/v1" or no "/v1" suffix at all).
    expect(addedLine).toMatch(/\/v1",?\s*(#.*)?$/);

    // No invented header name literal anywhere in the sample (the SDK's own
    // api_key param IS the Authorization: Bearer equivalent — never re-stated
    // under a different, unverified header name).
    expect(swap.textContent ?? "").not.toMatch(/X-Api-Key|Api-Key:/i);
  });
});

// ── R6 — Reject: raw or new visual pattern ──────────────────────────────────
describe("test_reject_raw_value", () => {
  it("every color/spacing/radius value traces to an existing token or component, never a raw literal", () => {
    const container = renderHomepage();
    const swap = within(container as unknown as HTMLElement).getByTestId("base-url-swap");
    expect(swap.outerHTML).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(swap.outerHTML).not.toMatch(/rgb\(|rgba\(/);
    expect(swap.querySelectorAll("[style]").length).toBe(0);
  });
});
