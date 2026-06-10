import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    environmentOptions: {
      jsdom: {
        // A real URL is required for jsdom to activate the localStorage Storage
        // interface (spec: origin-keyed storage only works for http/https origins)
        url: "http://localhost:3000",
      },
    },
    globals: true,
    projects: [
      {
        // tests/ suite: legacy gateway-direct auth; uses tests/mocks/server only
        extends: true,
        test: {
          name: "legacy",
          include: ["tests/**/*.test.{ts,tsx}", "test-support/**/*.test.{ts,tsx}"],
          setupFiles: [
            "./tests/setup.ts",
            "./test-support/mock-cjs-navigation.ts",
            "./test-support/legacy-bff-compat.ts",
          ],
        },
      },
      {
        // tests-bff/ suite: BFF cookie auth; uses tests-bff/mocks/server only
        // setupFiles does NOT include tests/setup.ts so the legacy MSW server
        // does not conflict with the BFF gateway handlers.
        extends: true,
        test: {
          name: "bff",
          include: ["tests-bff/**/*.test.{ts,tsx}"],
          setupFiles: [
            "./tests-bff/setup.ts",
            "./test-support/mock-cjs-navigation.ts",
          ],
        },
      },
    ],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      thresholds: {
        lines: 80,
      },
      include: ["components/**/*.tsx", "lib/**/*.ts"],
      exclude: ["**/*.test.tsx", "**/*.test.ts"],
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
});
