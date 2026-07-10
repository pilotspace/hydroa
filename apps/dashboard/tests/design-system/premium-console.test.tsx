/**
 * premium-console — RED contract for the confirmed "v7 premium" reconciliation
 * (Tin, 2026-07-06): the whole authenticated app adopts a DARK nav rail on a
 * WHITE-luxury canvas, a hero page-title scale, monospace/tabular numerals, and a
 * subtle film-grain — realized through the shared primitives so every route
 * inherits it (no 28-route rewrite). Token facts are read from the REAL globals.css
 * so the test tracks the shipped values, not a copy.
 *
 * RED before build: the light-mode `--sidebar` is still white (#ffffff), the canvas
 * is slate-50, there is no `--font-mono` token or `.app-grain` overlay, PageHeader
 * has no eyebrow/meta and renders a 24px title, and StatCard values are not mono.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import React from "react";

import { PageHeader } from "@/components/ui/page-header";
import { StatCard } from "@/components/ui/stat-card";

const css = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");

/** The `:root { … }` block only (the shipped LIGHT values), same anchor the aurora test uses. */
function rootBlock(): string {
  const start = css.indexOf(":root {");
  const end = css.indexOf("}", start);
  return css.slice(start, end);
}
function hexOf(token: string, scope = rootBlock()): string {
  const m = scope.match(new RegExp(`--${token}:\\s*(#[0-9a-fA-F]{6})`));
  if (!m) throw new Error(`--${token} not found`);
  return m[1];
}

// WCAG relative-luminance contrast (same math as contrast-audit.test.ts).
function channelLinear(c8: number): number {
  const s = c8 / 255;
  return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}
function luminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  return (
    0.2126 * channelLinear((n >> 16) & 255) +
    0.7152 * channelLinear((n >> 8) & 255) +
    0.0722 * channelLinear(n & 255)
  );
}
function contrast(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

// Direction landed (Tin 2026-07-07): after seeing the dark v7 rail live, reverted to the
// LIGHT Aurora rail but KEPT the theme-independent modern-tech upgrades — white canvas, hero
// titles, monospace tabular numerals, hairlines, grouped nav. (Dark rail + film grain dropped.)
describe("modern light Aurora rail + modern-tech keepers", () => {
  it("the sidebar surface is a LIGHT Aurora rail (not a dark rail)", () => {
    const hex = hexOf("sidebar");
    const n = parseInt(hex.slice(1), 16);
    const minChannel = Math.min((n >> 16) & 255, (n >> 8) & 255, n & 255);
    expect(minChannel).toBeGreaterThan(0xe0); // near-white light rail, not near-black
  });

  it("the canvas background is pure white (modern-tech luxury) — KEEPER", () => {
    expect(rootBlock()).toMatch(/--background:\s*#ffffff/i);
  });

  it("rail text clears WCAG AA (4.5:1) on the light rail", () => {
    expect(contrast(hexOf("sidebar-foreground"), hexOf("sidebar"))).toBeGreaterThanOrEqual(4.5);
  });

  it("the Classic-Blue active-item text clears AA on the accent-soft fill", () => {
    expect(contrast(hexOf("primary"), hexOf("accent-soft"))).toBeGreaterThanOrEqual(4.5);
  });

  it("a monospace token exists for tabular numerals + is bridged to font-mono — KEEPER", () => {
    expect(css).toMatch(/--font-mono:\s*ui-monospace/i);
  });

  it("the dark-rail film-grain overlay is dropped (clean modern-tech surface)", () => {
    expect(css).not.toMatch(/\.app-grain\b/);
  });

  it("Classic Blue identity is preserved (never reinvented)", () => {
    expect(rootBlock()).toMatch(/--primary:\s*#0f4c81/i);
  });
});

describe("PageHeader — premium hero header", () => {
  it("renders an optional eyebrow above the title, h1 still a direct child of the flex row", () => {
    render(<PageHeader eyebrow="Platform" title="Tenants" />);
    expect(screen.getByText("Platform")).toBeInTheDocument();
    const h1 = screen.getByRole("heading", { level: 1, name: "Tenants" });
    expect(document.querySelectorAll("h1")).toHaveLength(1);
    expect(h1.parentElement?.tagName.toLowerCase()).toBe("div");
    expect(h1.parentElement?.className).toContain("flex");
  });

  it("renders an optional mono spec-strip (meta)", () => {
    render(<PageHeader title="Tenants" meta={<span>0004 tenants</span>} />);
    expect(screen.getByText("0004 tenants")).toBeInTheDocument();
  });

  it("the title uses the display type scale (hero)", () => {
    render(<PageHeader title="Tenants" />);
    expect(screen.getByRole("heading", { level: 1, name: "Tenants" }).className).toContain(
      "text-display",
    );
  });

  it("omitting eyebrow/meta stays byte-compatible — a bare header emits no paragraph", () => {
    render(<PageHeader title="Upstream Health" />);
    expect(screen.queryByRole("paragraph")).not.toBeInTheDocument();
  });
});

describe("StatCard — spec-sheet numerals", () => {
  it("renders the value in a monospace tabular face", () => {
    render(<StatCard label="Spent this month" value="$312.47" />);
    const value = screen.getByText("$312.47");
    expect(value.className).toContain("font-mono");
    expect(value.className).toContain("tabular-nums");
  });
});
