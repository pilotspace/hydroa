/**
 * LIVE kind-cluster dashboard UI e2e — v53 task 9 (e2e-ui §1–§3).
 *
 * Drives the SHIPPED dashboard UI through the LIVE Envoy edge (TLS, self-signed) against the
 * running kind cluster — the browser analog of the API kind e2e (task 8). NO mocks, NO page.route:
 * a real login → real BFF → real gateway → real session → a real authenticated surface.
 *
 *   M1 a real login through the edge lands on /app/keys
 *   M2 the keys surface renders real (empty) gateway data for the fresh tenant
 *   R1 the guard redirects an unauthenticated browser to /login
 *   R2 the login form rejects a bad password (stays on /login + a visible error)
 *
 * Runs ONLY under `playwright.kind.config.ts` (testDir ./e2e-kind, baseURL the kind edge,
 * ignoreHTTPSErrors) via `make kind-e2e-ui`. The vitest floor + the a11y harness never collect it.
 */

import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

const PASSWORD = "hunter2hunter"; // ≥10 chars (signup zod min-10)
// Letters-only TLD: the email must satisfy BOTH the gateway AND the dashboard's client-side zod
// `.email()` — zod 3.25 rejects a digit-bearing TLD like `kind.e2e` ("Invalid email address"), so
// the login form would never submit. `kind.example` passes both (gateway signup 201, bad-pw 401).
const EMAIL_DOMAIN = "kind.example";
const LOGIN_PATH = "/login";
const KEYS_PATH = "/app/keys";

/** Unique-per-run tenant/email so the suite is re-runnable (idempotent against the live DB). */
function unique(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Mint a fresh tenant + owner OUT OF BAND through the edge (the seed; the browser then logs in). */
async function seedTenant(request: APIRequestContext): Promise<{ email: string }> {
  const sfx = unique();
  const email = `ui-${sfx}@${EMAIL_DOMAIN}`;
  const resp = await request.post("/api/auth/signup", {
    data: { tenant_name: `E2eUI-${sfx}`, email, password: PASSWORD },
  });
  expect(resp.status(), `signup through the edge should 201 (got ${resp.status()}): ${await resp.text()}`).toBe(201);
  return { email };
}

/** Drive the real login form: navigate, fill the credential inputs, submit. */
async function submitLogin(page: Page, email: string, password: string): Promise<void> {
  await page.goto(LOGIN_PATH);
  await page.locator("#login_email").fill(email);
  await page.locator("#login_password").fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
}

test.describe("dashboard UI through the live kind edge", () => {
  test("a real login through the edge lands on the keys surface (M1+M2)", async ({ page, request }) => {
    const { email } = await seedTenant(request);

    await submitLogin(page, email, PASSWORD);

    // M1: the login set the session and the browser navigated to /app/keys (NOT bounced to /login).
    await page.waitForURL(`**${KEYS_PATH}`);
    await expect(page).toHaveURL(new RegExp(`${KEYS_PATH}$`));

    // M2: the authed keys surface rendered the real (empty) key list for the fresh tenant.
    await expect(page.getByRole("heading", { level: 1, name: /API Keys/i })).toBeVisible();
    await expect(page.getByText("No API keys yet")).toBeVisible();
    await expect(page.getByRole("navigation")).toBeVisible();
  });

  test("the guard redirects the unauthenticated browser (R1)", async ({ page }) => {
    // A fresh Playwright context has no cookies — the dashboard guard must bounce us to /login.
    await page.goto(KEYS_PATH);
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { level: 1, name: /API Keys/i })).toHaveCount(0);
  });

  test("the login form rejects a bad password (R2)", async ({ page, request }) => {
    const { email } = await seedTenant(request);

    await submitLogin(page, email, `wrong-${PASSWORD}`);

    // The error must be the SERVER's auth rejection (gateway 401 → BFF), NOT a client zod alert —
    // the email is valid, so "Invalid email address" must NOT appear (guards the v2 fixture bug).
    await expect(page.getByText("Invalid email address")).toHaveCount(0);
    await expect(page.getByRole("alert").first()).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
  });
});
