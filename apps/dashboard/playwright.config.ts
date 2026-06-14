import { defineConfig } from "@playwright/test";

/**
 * Standalone real-browser a11y harness (v17 realbrowser-a11y-pass).
 *
 * This is DELIBERATELY separate from the vitest floor: it needs a downloaded
 * Chromium (`npx playwright install chromium`, ~92 MiB, NOT committed) and a
 * prod-built server, so it must never gate `npm test`. Run it with
 * `npm run test:a11y`. It proves color-contrast + true-layout a11y that
 * jsdom-axe cannot evaluate.
 *
 * The webServer builds + serves the prod app on 127.0.0.1; the spec renders the
 * authed surfaces WITHOUT a gateway via Playwright cookie-seed + page.route()
 * interception of the same-origin /api/** fetches.
 */

const PORT = 3100;
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e-a11y",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: BASE_URL,
    viewport: { width: 1280, height: 800 },
    headless: true,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: {
    // GATEWAY_URL is irrelevant here (the BFF /api/** calls are intercepted at
    // the browser before they reach the Next server) but we pin a dead loopback
    // address so nothing can accidentally reach a real upstream.
    command: `GATEWAY_URL=http://127.0.0.1:1 npm run build && GATEWAY_URL=http://127.0.0.1:1 npm run start -- -p ${PORT}`,
    url: `${BASE_URL}/login`,
    timeout: 180_000,
    reuseExistingServer: true,
    stdout: "pipe",
    stderr: "pipe",
  },
});
