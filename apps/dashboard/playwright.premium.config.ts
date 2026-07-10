import { defineConfig } from "@playwright/test";

/**
 * playwright.premium.config.ts — TEMPORARY visual-proof harness (not committed).
 * Points at the ALREADY-RUNNING dev server on :3300 (no webServer block) and
 * captures the v7-premium surfaces + runs axe with color-contrast ON to prove the
 * dark rail clears AA. Run:
 *   ./node_modules/.bin/playwright test --config playwright.premium.config.ts
 */
const BASE_URL = "http://127.0.0.1:3300";

export default defineConfig({
  testDir: "./e2e-review",
  testMatch: /premium\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  timeout: 240_000,
  expect: { timeout: 15_000 },
  use: { baseURL: BASE_URL, headless: true },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
