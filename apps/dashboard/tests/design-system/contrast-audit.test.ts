/**
 * contrast-audit.test.ts — WCAG 2.2 AA contrast guard for the secondary-text token.
 *
 * The persona UI/UX audit found `--muted-foreground` (slate-500) failing AA on the
 * tinted `bg-muted/30` grounds the dashboard uses for its canvas and cards — the
 * single largest class of axe color-contrast violations (≈100 nodes across 28
 * routes). This test pins the fix: muted secondary text must clear 4.5:1 against
 * every light surface token it actually renders on. Parses the REAL globals.css so
 * it tracks the shipped token, not a copy.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const css = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");

/** First `--token: #rrggbb` occurrence = the light (`:root`) value. */
function firstHex(token: string): string {
  const m = css.match(new RegExp(`--${token}:\\s*(#[0-9a-fA-F]{6})`));
  if (!m) throw new Error(`token --${token} not found in globals.css`);
  return m[1];
}

function channelLinear(c8: number): number {
  const s = c8 / 255;
  return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}
function luminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  return 0.2126 * channelLinear(r) + 0.7152 * channelLinear(g) + 0.0722 * channelLinear(b);
}
function contrast(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  const hi = Math.max(la, lb);
  const lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

describe("WCAG 2.2 AA — --muted-foreground is legible on every light ground", () => {
  const fg = firstHex("muted-foreground");
  // --muted (slate-100) is the darkest light surface, so it is the strictest
  // ground; --background and --card are lighter (easier). bg-muted/30 tints are
  // lighter than solid --muted, so passing here covers them too.
  for (const ground of ["muted", "background", "card"]) {
    it(`--muted-foreground on --${ground} meets 4.5:1`, () => {
      const ratio = contrast(fg, firstHex(ground));
      expect(ratio).toBeGreaterThanOrEqual(4.5);
    });
  }
});
