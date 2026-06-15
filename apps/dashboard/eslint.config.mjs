// Flat ESLint config for Next.js 16.
//
// Next 16 removes `next lint`; linting runs via the ESLint CLI (`eslint .`).
// eslint-config-next 16 ships a NATIVE flat config — consume it directly
// (`eslint-config-next/core-web-vitals`) rather than via the FlatCompat shim,
// which chokes on the config's circular plugin references under ESLint 9.
//
// Scope mirrors the prior `next lint` behavior: lint the production source
// (app/ · components/ · lib/) and ignore build artifacts + the test trees
// (the vitest suites are the test gate; they use msw/CJS patterns ESLint's
// app rules don't apply to).

import { defineConfig, globalIgnores } from "eslint/config";
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";

export default defineConfig([
  globalIgnores([
    ".next/**",
    "coverage/**",
    "node_modules/**",
    "tests/**",
    "tests-bff/**",
    "next-env.d.ts",
    "*.config.*",
  ]),
  ...nextCoreWebVitals,
  {
    // eslint-config-next 16 enables these React-Compiler-era rules. v14 (Next 16)
    // temporarily downgraded them error→warn (a DECLARED + TICKETED transition) while the
    // pre-existing v13/v15 patterns were fixed. v17 `react-hooks-strict-lint` discharged
    // that ticket: SpendPage holds last-good-on-error in STATE (no ref read in render),
    // use-focus-trap writes its callback ref in an effect, and the settings tabs seed form
    // state via a guarded adjust-during-render (no setState-in-effect). The rules are now
    // PINNED at "error" — the strict-gate ratchet (foundation v16): a later task may not
    // silently re-loosen them (tests-bff/lint-rules-strict.test.ts guards this invariant).
    rules: {
      "react-hooks/refs": "error",
      "react-hooks/set-state-in-effect": "error",
    },
  },
]);
