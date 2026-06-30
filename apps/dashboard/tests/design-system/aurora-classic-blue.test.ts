/**
 * aurora-classic-blue (v54 · aurora-polish-tokens) — RED contract for the Hydroa
 * "Classic Blue" luxury rebrand: the brand accent moves indigo → Pantone Classic Blue
 * (#0F4C81, the "blue of the year"), a deep→bright blue brand gradient, and the gradient
 * wired into the shared SidebarBrand logo tile. Token-led: tokens.json (source) and
 * globals.css (realization) must stay in sync.
 *
 * RED before build: globals.css still has indigo (#4f46e5), tokens.json still aliases
 * indigo-600, and SidebarBrand has no gradient tile.
 */
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import React from "react";

import { SidebarBrand } from "@/components/ui/sidebar";

const REPO_ROOT = resolve(process.cwd(), "../..");
const GLOBALS = resolve(process.cwd(), "app/globals.css");
const TOKENS = resolve(REPO_ROOT, ".add/design/tokens.json");
const css = () => readFileSync(GLOBALS, "utf8");

/** Pull the `:root { … }` block so we assert the LIGHT (shipped) values, not `.dark`. */
function rootBlock(): string {
  const s = css();
  const start = s.indexOf(":root {"); // anchor on the SELECTOR, not the ":root" mention in the header comment
  const end = s.indexOf("}", start);
  return s.slice(start, end);
}

describe("aurora Classic Blue — realized CSS (globals.css :root)", () => {
  it("test_primary_is_classic_blue", () => {
    expect(rootBlock()).toMatch(/--primary:\s*#0f4c81/i);
  });

  it("test_brand_gradient_is_deep_to_bright_blue", () => {
    const root = rootBlock();
    expect(root).toMatch(/--brand-from:\s*#0f4c81/i);
    expect(root).toMatch(/--brand-to:\s*#2563eb/i);
  });

  it("test_focus_ring_is_blue", () => {
    expect(rootBlock()).toMatch(/--ring:\s*#2563eb/i);
  });

  it("test_no_indigo_accent_remains_in_root", () => {
    // the OLD indigo brand literal must be fully gone from the shipped :root — guards a partial
    // migration where only --primary changed but --ring/--chart-1/etc. were left indigo.
    const root = rootBlock();
    expect(root).not.toMatch(/#4f46e5/i); // indigo-600
    expect(root).not.toMatch(/#6366f1/i); // indigo-500 (old ring)
  });
});

describe("aurora Classic Blue — DTCG source (tokens.json) in sync", () => {
  it("test_blue_primitive_ramp_added", () => {
    const t = JSON.parse(readFileSync(TOKENS, "utf8"));
    const blue = t?.primitive?.color?.blue;
    expect(blue).toBeTruthy();
    expect(String(blue?.brand?.$value).toUpperCase()).toBe("#0F4C81");
  });

  it("test_accent_aliases_blue_ramp", () => {
    const t = JSON.parse(readFileSync(TOKENS, "utf8"));
    const accent = t?.semantic?.color?.accent?.$value;
    expect(String(accent)).toMatch(/primitive\.color\.blue/);
  });
});

describe("aurora Classic Blue — gradient wired to the shared SidebarBrand", () => {
  it("test_sidebarbrand_logo_tile_uses_brand_gradient", () => {
    const { container } = render(
      React.createElement(SidebarBrand, { title: "Hydroa", icon: React.createElement("svg") }),
    );
    const tile = container.querySelector('[class*="from-brand-from"]');
    expect(tile).not.toBeNull();
    expect(tile?.className).toMatch(/to-brand-to/);
  });
});
