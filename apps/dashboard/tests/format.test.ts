/**
 * tests/format.test.ts — RED for the shared lib/format date helper.
 *
 * The admin read tables (alerts / audit / usage) rendered raw ISO 8601 strings.
 * formatTimestamp() is the one shared humanizer they now route through.
 *
 * RED before build: `@/lib/format` does not exist yet → MODULE_NOT_FOUND.
 */
import { describe, it, expect } from "vitest";
import { formatTimestamp, formatNumber, formatUsd, formatBucketLabel } from "@/lib/format";

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

describe("formatNumber", () => {
  it("adds locale thousands separators for large counts", () => {
    expect(formatNumber(1234567)).toBe("1,234,567");
  });

  it("leaves small numbers unseparated (frozen usage suite renders 3/300/150)", () => {
    expect(formatNumber(3)).toBe("3");
    expect(formatNumber(300)).toBe("300");
    expect(formatNumber(150)).toBe("150");
  });

  it("degrades to an em dash for nullish / non-finite (never NaN)", () => {
    expect(formatNumber(null)).toBe("—");
    expect(formatNumber(undefined)).toBe("—");
    expect(formatNumber(Number.NaN)).toBe("—");
  });
});

describe("formatUsd", () => {
  it("renders a currency string with a symbol and 2 decimals", () => {
    expect(formatUsd("1.23")).toBe("$1.23");
    expect(formatUsd(1234.5)).toBe("$1,234.50");
    expect(formatUsd("0")).toBe("$0.00");
  });

  it("accepts a numeric or string amount identically", () => {
    expect(formatUsd(1.23)).toBe(formatUsd("1.23"));
  });

  it("degrades to an em dash for nullish / empty", () => {
    expect(formatUsd(null)).toBe("—");
    expect(formatUsd(undefined)).toBe("—");
    expect(formatUsd("")).toBe("—");
  });

  it("falls back to the raw string when unparseable (never $NaN)", () => {
    expect(formatUsd("n/a")).toBe("n/a");
  });
});

describe("formatBucketLabel", () => {
  it("humanizes a date bucket to a short month/day (no raw ISO survives)", () => {
    const out = formatBucketLabel("2026-06-10T00:00:00Z", "day");
    expect(out).toMatch(/Jun/);
    expect(out).toContain("10");
    expect(out).not.toContain("2026-06-10T00:00:00Z");
    expect(out).not.toMatch(/T\d\d:\d\d/);
  });

  it("renders a month window as month + year", () => {
    expect(formatBucketLabel("2026-06-01T00:00:00Z", "month")).toBe("Jun 2026");
  });

  it("is UTC-stable — a date-only bucket never slips a day", () => {
    // "2026-06-01" (no time) must read as Jun 1 regardless of the runner's timezone.
    expect(formatBucketLabel("2026-06-01", "day")).toMatch(/Jun 1\b/);
  });

  it("falls back to the raw value when unparseable", () => {
    expect(formatBucketLabel("nope", "day")).toBe("nope");
  });
});
