import { defineConfig } from "@playwright/test";

/**
 * playwright.review.config.ts — TEMPORARY UI/UX review harness (not committed).
 *
 * Boots the prod-built dashboard on 127.0.0.1:3200 and renders every in-scope
 * surface per persona WITHOUT a gateway: a seeded session cookie + page.route()
 * interception of the same-origin /api/** fetches (same proven pattern as
 * e2e-a11y/a11y.spec.ts). The spec captures desktop+mobile screenshots and runs
 * axe-core per route, writing all evidence to the session scratchpad.
 *
 * Run:  npx playwright test --config playwright.review.config.ts
 */

const PORT = 3200;
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e-review",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  timeout: 240_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: BASE_URL,
    headless: true,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: {
    // GATEWAY_URL pinned to a dead loopback — every /api/** call is intercepted
    // in the browser before it reaches the Next server, so nothing hits upstream.
    command: `GATEWAY_URL=http://127.0.0.1:1 npm run start -- -p ${PORT}`,
    url: `${BASE_URL}/login`,
    timeout: 180_000,
    reuseExistingServer: true,
    stdout: "pipe",
    stderr: "pipe",
  },
});
