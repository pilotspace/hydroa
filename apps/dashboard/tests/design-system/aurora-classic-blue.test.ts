/**
 * airier-azure (was v54 "aurora-classic-blue") — the brand-accent identity contract.
 *
 * SUPERSEDED IDENTITY: this file originally pinned the v54 "Pantone Classic Blue"
 * (#0F4C81) rebrand. The dashboard-hallmark-restyle "Airier" direction (Tin-locked
 * 2026-07-17, shipped in the whole-dashboard restyle + re-frozen foundation) moved the
 * brand accent to AZURE (#2F6DF0), renamed the primitive ramp blue→azure, and kept the
 * deep→bright brand gradient. Retargeted to the shipped Airier values — the STRUCTURAL
 * guards are unchanged (primary pinned to a single brand hex · deep→bright gradient wired
 * to SidebarBrand · tokens.json ↔ globals.css in sync · no stale indigo/classic-blue).
 * Token-led: tokens.json (source) and globals.css (realization) must stay in sync.
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

describe("airier azure — realized CSS (globals.css :root)", () => {
  it("test_primary_is_azure", () => {
    expect(rootBlock()).toMatch(/--primary:\s*#2f6df0/i);
  });

  it("test_brand_gradient_is_deep_to_bright_azure", () => {
    const root = rootBlock();
    expect(root).toMatch(/--brand-from:\s*#2f6df0/i);
    expect(root).toMatch(/--brand-to:\s*#5b8cff/i);
  });

  it("test_focus_ring_is_azure", () => {
    expect(rootBlock()).toMatch(/--ring:\s*#2f6df0/i);
  });

  it("test_no_indigo_or_classic_blue_accent_remains_in_root", () => {
    // the OLD brand literals must be fully gone from the shipped :root — guards a partial
    // migration where only --primary changed but --ring/--chart-1/etc. were left behind.
    const root = rootBlock();
    expect(root).not.toMatch(/#4f46e5/i); // indigo-600 (pre-v54)
    expect(root).not.toMatch(/#6366f1/i); // indigo-500 (pre-v54 ring)
    expect(root).not.toMatch(/#0f4c81/i); // Pantone Classic Blue (v54, superseded by Airier)
  });
});

describe("airier azure — DTCG source (tokens.json) in sync", () => {
  it("test_azure_primitive_ramp_added", () => {
    const t = JSON.parse(readFileSync(TOKENS, "utf8"));
    const azure = t?.primitive?.color?.azure;
    expect(azure).toBeTruthy();
    expect(String(azure?.["500"]?.$value).toUpperCase()).toBe("#2F6DF0");
  });

  it("test_accent_aliases_azure_ramp", () => {
    const t = JSON.parse(readFileSync(TOKENS, "utf8"));
    const accent = t?.semantic?.color?.accent?.$value;
    expect(String(accent)).toMatch(/primitive\.color\.azure/);
  });
});

describe("airier azure — gradient wired to the shared SidebarBrand", () => {
  it("test_sidebarbrand_logo_tile_uses_brand_gradient", () => {
    const { container } = render(
      React.createElement(SidebarBrand, { title: "Hydroa", icon: React.createElement("svg") }),
    );
    const tile = container.querySelector('[class*="from-brand-from"]');
    expect(tile).not.toBeNull();
    expect(tile?.className).toMatch(/to-brand-to/);
  });
});
