import { defineConfig } from "@playwright/test";

/** THROWAWAY polish-review harness — points at the running dev server on :3300. */
export default defineConfig({
  testDir: "./e2e-review",
  testMatch: /(polish|debug)\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  timeout: 240_000,
  expect: { timeout: 15_000 },
  use: { baseURL: "http://127.0.0.1:3300", headless: true },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
