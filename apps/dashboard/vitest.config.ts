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
    setupFiles: ["./tests/setup.ts"],
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
