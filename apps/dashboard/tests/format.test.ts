/**
 * tests/format.test.ts — RED for the shared lib/format date helper.
 *
 * The admin read tables (alerts / audit / usage) rendered raw ISO 8601 strings.
 * formatTimestamp() is the one shared humanizer they now route through.
 *
 * RED before build: `@/lib/format` does not exist yet → MODULE_NOT_FOUND.
 */
import { describe, it, expect } from "vitest";
import { formatTimestamp } from "@/lib/format";

describe("formatTimestamp", () => {
  it("humanizes an ISO timestamp — no raw T/Z marker survives", () => {
    const out = formatTimestamp("2026-06-10T12:05:00Z");
    expect(out).toContain("2026");
    // raw machine form must be gone (timezone-robust: only assert the year)
    expect(out).not.toContain("2026-06-10T12:05:00Z");
    expect(out).not.toMatch(/T\d\d:\d\d/);
  });

  it("renders an em dash for null / undefined / empty", () => {
    expect(formatTimestamp(null)).toBe("—");
    expect(formatTimestamp(undefined)).toBe("—");
    expect(formatTimestamp("")).toBe("—");
  });

  it("falls back to the raw value when unparseable (never throws, never blank)", () => {
    expect(formatTimestamp("not-a-date")).toBe("not-a-date");
  });
});
