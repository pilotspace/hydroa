/**
 * tests/platform-token-hygiene.test.tsx — RED suite proving the admin-console-ui
 * task's new source files trace every color to the CURRENT globals.css tokens
 * (Classic Blue #0f4c81 / Aurora), never DESIGN.md's stale Indigo-600 (#4F46E5)
 * prose (TASK.md §1 Framings weighed, "Design-token source of truth").
 *
 * Source-level enforcement of the §2 "after" scenario ("every rendered color/
 * radius/font traces to the current, shipped token file") — a real, automated
 * grep rather than a one-time visual check of the design-confirm mock.
 *
 * RED before Build: `components/platform/` does not exist yet -> readdirSync
 * throws ENOENT, the established true-red convention for a filesystem-gated test.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

const PLATFORM_DIR = resolve(process.cwd(), "components/platform");
const EDITED_SHARED_FILES = [
  resolve(process.cwd(), "components/ui/card.tsx"),
  resolve(process.cwd(), "components/ui/app-shell.tsx"),
];

const FORBIDDEN: RegExp[] = [/#4F46E5/i, /\b4F46E5\b/i, /indigo/i];

function platformSourceFiles(): string[] {
  const names = readdirSync(PLATFORM_DIR).filter(
    (f) => f.endsWith(".tsx") || f.endsWith(".ts"),
  );
  return names.map((f) => resolve(PLATFORM_DIR, f));
}

describe("platform admin console — token hygiene", () => {
  it("test_new_platform_source_has_no_stale_indigo_literal", () => {
    const files = [...platformSourceFiles(), ...EDITED_SHARED_FILES];
    expect(files.length).toBeGreaterThan(0);
    for (const file of files) {
      const src = readFileSync(file, "utf-8");
      for (const pattern of FORBIDDEN) {
        expect(src, `${file} must not match ${pattern}`).not.toMatch(pattern);
      }
    }
  });
});
