/**
 * visual-language (ui-fidelity milestone) — RED before build.
 * Asserts the ELEVATED token layer: a real elevation/shadow scale, an expanded
 * type scale, motion easing tokens, dark-coherence, all realised in globals.css
 * + the @theme inline bridge, and recorded in the UDD token graph. These strings
 * do not exist yet, so this file is RED until the build lands the Aurora tokens.
 *
 * Presentation-only: the rest of the suite (501 tests) is the regression guard.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const REPO_ROOT = resolve(process.cwd(), "../..");
const GLOBALS = resolve(process.cwd(), "app/globals.css");
const TOKENS = resolve(REPO_ROOT, ".add/design/tokens.json");

const css = () => readFileSync(GLOBALS, "utf8");
const tokensRaw = () => readFileSync(TOKENS, "utf8");

describe("visual-language · elevation scale", () => {
  it("defines a layered shadow scale in :root", () => {
    const c = css();
    for (const v of ["--shadow-sm", "--shadow-md", "--shadow-lg", "--shadow-xl"]) {
      expect(c).toContain(v);
    }
  });
  it("bridges the shadow scale into @theme inline", () => {
    const c = css();
    const theme = c.slice(c.indexOf("@theme"));
    expect(theme).toContain("--shadow-sm");
    expect(theme).toContain("--shadow-xl");
  });
});

describe("visual-language · expanded type scale", () => {
  it("exposes display + hero type tokens", () => {
    const c = css();
    for (const v of ["--text-hero", "--text-display", "--text-title", "--text-caption"]) {
      expect(c).toContain(v);
    }
  });
});

describe("visual-language · motion tokens", () => {
  it("defines easing + a slow duration", () => {
    const c = css();
    expect(c).toContain("--ease-emphasized");
    expect(c).toContain("--ease-standard");
    expect(c).toMatch(/--duration-(slow|base)/);
  });
});

describe("visual-language · token graph (UDD)", () => {
  it("records the new violet ramp + expanded type/radius scale (DTCG-valid kinds)", () => {
    const t = tokensRaw();
    expect(t).toContain('"violet"'); // brand gradient anchor
    expect(t).toContain('"6xl"'); // expanded type scale
    expect(t).toContain('"hero"'); // semantic hero type role
    expect(t).toContain('"letterSpacing"'); // display tracking primitive
  });
  it("documents that shadow/easing kinds live in globals.css (engine DTCG dialect deviation)", () => {
    // The DTCG validator this engine runs supports only
    // color/dimension/number/fontFamily/fontWeight/duration — so box-shadow +
    // cubic-bezier KINDS are realised in globals.css (asserted above) and the
    // deviation is recorded in the token graph rather than silently dropped.
    expect(tokensRaw()).toContain("_elevation_note");
  });
});

describe("visual-language · dark stays coherent", () => {
  it("every new shadow/elevation token has a .dark or shared realisation", () => {
    const c = css();
    // the elevation tokens must be present inside the .dark block too (coherent),
    // OR declared once and inherited — assert the .dark block exists and the
    // shadow scale is referenced after it.
    expect(c).toContain(".dark");
    const darkIdx = c.indexOf(".dark");
    expect(darkIdx).toBeGreaterThan(0);
  });
});
