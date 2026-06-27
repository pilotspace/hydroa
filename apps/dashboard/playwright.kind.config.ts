import { defineConfig } from "@playwright/test";

/**
 * LIVE kind-edge browser e2e harness (v53 task e2e-ui).
 *
 * DELIBERATELY separate from the a11y harness (`playwright.config.ts`, testDir `e2e-a11y/`,
 * which mocks the gateway on a local server) and from the vitest floor. This config drives the
 * REAL in-cluster dashboard through the live Envoy edge — there is NO `webServer`; the dashboard
 * already runs in the kind cluster behind Envoy's self-signed TLS.
 *
 * Run it with `make kind-e2e-ui` (ensures the cluster is Ready + Chromium is installed). The edge
 * URL is overridable via KIND_EDGE_URL for CI / a non-default NodePort.
 */

const EDGE_URL = process.env.KIND_EDGE_URL ?? "https://127.0.0.1:8443";

export default defineConfig({
  testDir: "./e2e-kind",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: EDGE_URL,
    // The kind edge serves a self-signed cert (the harness mints `ai-proxy-edge-tls`); accept it.
    ignoreHTTPSErrors: true,
    viewport: { width: 1280, height: 800 },
    headless: true,
    // Capture artifacts on failure for diagnosis (test-results/ is git-ignored + scope-excluded).
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  // No webServer: the dashboard under test is the in-cluster pod reached through the Envoy edge.
});
