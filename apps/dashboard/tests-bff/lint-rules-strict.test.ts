/**
 * tests-bff/lint-rules-strict.test.ts — the react-hooks strictness ratchet guard.
 *
 * v17 task react-hooks-strict-lint flips the two React-Compiler rules that v14
 * (Next 16) downgraded error→warn back to "error", after fixing the 60 flagged
 * patterns behavior-preservingly. The milestone "strict-gate ratchet" decision
 * says: once tightened, they must STAY tightened — a later task may not silently
 * re-loosen them. This structural guard pins that invariant.
 *
 * RED before Build: eslint.config.mjs still sets both rules to "warn" → fails.
 * GREEN after Build: both rules are "error" with no "warn" downgrade for them.
 *
 * Static/source suite (the rule posture's "shape" is the config source text) —
 * mirrors strict-harness.test.ts.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const TESTS_BFF_ROOT = dirname(fileURLToPath(import.meta.url));

function eslintConfigSource(): string {
  return readFileSync(join(TESTS_BFF_ROOT, "..", "eslint.config.mjs"), "utf8");
}

describe("react-hooks strictness ratchet — eslint.config.mjs", () => {
  it("test_refs_rule_is_error", () => {
    const cfg = eslintConfigSource();
    // The ref-in-render rule must be pinned at "error" (the ratchet).
    expect(cfg).toMatch(/["']react-hooks\/refs["']\s*:\s*["']error["']/);
    // …and never re-downgraded to "warn".
    expect(cfg).not.toMatch(/["']react-hooks\/refs["']\s*:\s*["']warn["']/);
  });

  it("test_set_state_in_effect_rule_is_error", () => {
    const cfg = eslintConfigSource();
    expect(cfg).toMatch(/["']react-hooks\/set-state-in-effect["']\s*:\s*["']error["']/);
    expect(cfg).not.toMatch(/["']react-hooks\/set-state-in-effect["']\s*:\s*["']warn["']/);
  });
});
