/**
 * e2e-a11y/a11y.spec.ts — the v17 real-browser axe pass over the primary surfaces.
 *
 * Discharges the standing v13/v15 residue: a REAL headless Chromium (desktop
 * viewport) axe run that evaluates color-contrast + true layout — the rules
 * jsdom-axe cannot check. Covers /login (public) + the four authed design-system
 * pages, rendered WITHOUT a gateway via a seeded session cookie + page.route()
 * interception of the same-origin /api/** fetches (fixture shapes mirror tests-bff).
 *
 * Assertion: zero axe violations of impact serious OR critical, color-contrast
 * ENABLED. A real finding is FIXED at source (never by disabling a rule).
 */

import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// ── canned BFF fixtures (shapes mirror tests-bff/mocks/handlers.ts et al.) ──────
const ME = {
  user_id: "00000000-0000-0000-0000-000000000001",
  tenant_id: "00000000-0000-0000-0000-000000000099",
  email: "ada@acme.io",
  role: "owner",
  exp: 9999999999,
};
const USAGE = {
  total_cost_usd: "1.23",
  total_requests: 3,
  total_prompt_tokens: 300,
  total_completion_tokens: 150,
  records: [],
};
const BUDGET = { budget_usd_monthly: "25.00", spent_usd_month: "10.50" };
const MODELS = { object: "list", data: [] };
const KEYS = [
  {
    key_id: "kid-default",
    name: "default-key",
    prefix: "sk-default",
    created_at: "2026-01-01T00:00:00Z",
    revoked_at: null,
  },
];
const SPEND = {
  window: "month",
  bucket_size: "month",
  totals: {
    bucket_start: "2026-06-01T00:00:00Z",
    bucket_end: "2026-06-11T00:00:00Z",
    requests: 3,
    prompt_tokens: 300,
    completion_tokens: 150,
    cost_usd: "1.23",
  },
  buckets: [
    {
      bucket_start: "2026-06-01T00:00:00Z",
      requests: 2,
      prompt_tokens: 200,
      completion_tokens: 100,
      cost_usd: "0.80",
    },
  ],
  breakdown: null,
};
const CACHE = { enabled: true, semantic_enabled: false };

function gwBody(url: string): unknown {
  if (url.includes("/admin/usage")) return USAGE;
  if (url.includes("/admin/budget")) return BUDGET;
  if (url.includes("/admin/catalog/models")) return MODELS;
  if (url.includes("/admin/keys")) return KEYS;
  if (url.includes("/admin/spend")) return SPEND;
  if (url.includes("/admin/cache")) return CACHE;
  return {};
}

/** Intercept the same-origin BFF calls so authed pages render without a gateway. */
async function stubBff(page: Page): Promise<void> {
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: ME }));
  await page.route("**/api/gw/**", (route) =>
    route.fulfill({ json: gwBody(route.request().url()) }),
  );
}

/** A fake session cookie — the route guard is presence-only and /api/auth/me is stubbed. */
async function seedSession(page: Page): Promise<void> {
  await page.context().addCookies([
    { name: "ai_proxy_session", value: "e30.e30.fakesig", domain: "127.0.0.1", path: "/" },
  ]);
}

const BLOCKING = new Set(["serious", "critical"]);

async function blockingViolations(page: Page) {
  const results = await new AxeBuilder({ page }).analyze();
  return results.violations.filter((v) => BLOCKING.has(v.impact ?? ""));
}

test.describe("real-browser a11y — primary surfaces (WCAG 2.2 AA, color-contrast + true layout)", () => {
  test("/login is axe-clean", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: /sign in/i })).toBeVisible();
    const v = await blockingViolations(page);
    expect(v, `serious/critical axe violations on /login:\n${JSON.stringify(v, null, 2)}`).toEqual([]);
  });

  // Each authed surface + the exact <h1> its page component renders. Asserting the
  // heading proves the page mounted its REAL populated layout (the h1 only renders
  // on the success path — an error boundary shows ErrorState instead), so the axe
  // pass is never vacuous (it is not silently scanning a loading/error frame).
  const AUTHED: Array<{ path: string; heading: RegExp }> = [
    { path: "/app/usage", heading: /Usage & Cost Analytics/i },
    { path: "/app/keys", heading: /API Keys/i },
    { path: "/app/spend", heading: /Spend Analytics/i },
    { path: "/app/settings", heading: /Settings/i },
  ];

  for (const { path, heading } of AUTHED) {
    test(`${path} is axe-clean (stubbed authed render)`, async ({ page }) => {
      await seedSession(page);
      await stubBff(page);
      await page.goto(path);
      // The app shell (nav) + the page's own heading must both render so axe runs
      // on a real, populated layout (not a transient/loading/error frame).
      await expect(page.getByRole("navigation")).toBeVisible();
      await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
      await page.waitForLoadState("networkidle");
      const v = await blockingViolations(page);
      expect(v, `serious/critical axe violations on ${path}:\n${JSON.stringify(v, null, 2)}`).toEqual([]);
    });
  }
});
