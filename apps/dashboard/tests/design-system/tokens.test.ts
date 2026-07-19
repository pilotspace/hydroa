/**
 * design-system-foundation — UDD design-set + wiring + guard tests (file-level).
 * RED before build: the design set, the wired globals, components/ui, and the new
 * deps do not exist yet. The AUTHORITATIVE shape lint of the design set is
 * `python3 .add/tooling/add.py check`; these are the frontend-side mirrors that
 * keep the foundation honest inside `vitest run`.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, existsSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

// vitest cwd = apps/dashboard ; the UDD named set lives at repo-root/.add/design
const REPO_ROOT = resolve(process.cwd(), "../..");
const DESIGN = resolve(REPO_ROOT, ".add/design");
const DASH = process.cwd();

type TokenNode = { $value?: unknown; $type?: string } & Record<string, unknown>;

function readJson(path: string): Record<string, unknown> {
  return JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
}

/** Resolve a "{a.b.c}" alias chain against the token tree to a literal $value. */
function resolveAlias(tokens: Record<string, unknown>, value: unknown): unknown {
  let v = value;
  let hops = 0;
  while (typeof v === "string" && v.startsWith("{") && v.endsWith("}") && hops < 10) {
    const path = v.slice(1, -1).split(".");
    let node: unknown = tokens;
    for (const seg of path) node = (node as Record<string, unknown>)?.[seg];
    v = (node as TokenNode)?.$value;
    hops += 1;
  }
  return v;
}

describe("M1 · token set shape + identity", () => {
  it("tokens.json exists with the 3 layers", () => {
    expect(existsSync(resolve(DESIGN, "tokens.json"))).toBe(true);
    const t = readJson(resolve(DESIGN, "tokens.json"));
    expect(t).toHaveProperty("primitive");
    expect(t).toHaveProperty("semantic");
    expect(t).toHaveProperty("component");
  });

  it("semantic.color.accent resolves to azure #2F6DF0", () => {
    // Airier (dashboard-hallmark-restyle, Tin-locked 2026-07-17): the brand accent moved
    // Classic Blue (#0F4C81, v54) → AZURE (#2F6DF0, the "azure signal"). The identity pin is
    // PRESERVED at the new value, not removed — accent still resolves to a single, asserted
    // brand hex (now aliased through primitive.color.azure.500).
    const t = readJson(resolve(DESIGN, "tokens.json"));
    const semantic = (t.semantic as Record<string, Record<string, TokenNode>>) ?? {};
    const accent = semantic?.color?.accent?.$value;
    const resolved = resolveAlias(t, accent);
    expect(String(resolved).toUpperCase()).toBe("#2F6DF0");
  });

  it("the Geist typeface is the base sans family", () => {
    // Airier: the base sans moved Inter → Geist (the anti-slop, off-Inter technical grotesque).
    const t = readJson(resolve(DESIGN, "tokens.json"));
    const prim = t.primitive as Record<string, Record<string, Record<string, TokenNode>>>;
    const sans = prim?.font?.family?.sans?.$value as unknown[];
    expect(Array.isArray(sans) ? sans : []).toContain("Geist");
  });
});

describe("M2 · tokens wired as the single source", () => {
  it("globals.css declares an @theme token layer", () => {
    const css = readFileSync(resolve(DASH, "app/globals.css"), "utf8");
    expect(css).toMatch(/@theme/);
    expect(css).toMatch(/--color-/);
  });
});

describe("M6 · catalog + prototype present and self-consistent", () => {
  it("catalog.json + the foundation prototype exist", () => {
    expect(existsSync(resolve(DESIGN, "catalog.json"))).toBe(true);
    expect(existsSync(resolve(DESIGN, "prototypes/dashboard-foundation.json"))).toBe(true);
  });

  it("every prototype element type is a cataloged component and root resolves", () => {
    const catalog = readJson(resolve(DESIGN, "catalog.json"));
    const tree = readJson(resolve(DESIGN, "prototypes/dashboard-foundation.json"));
    const components = (catalog.components as Record<string, unknown>) ?? {};
    const elements = (tree.elements as Record<string, { type: string }>) ?? {};
    expect(Object.keys(elements)).toContain(tree.root as string);
    for (const el of Object.values(elements)) {
      expect(Object.keys(components)).toContain(el.type);
    }
  });
});

describe("R3 · no untokenized raw values in the primitive layer", () => {
  it("no components/ui source hardcodes a raw #hex or bare Npx a token covers", () => {
    const uiDir = resolve(DASH, "components/ui");
    expect(existsSync(uiDir)).toBe(true);
    const offenders: string[] = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const p = resolve(dir, entry.name);
        if (entry.isDirectory()) walk(p);
        else if (/\.(tsx?|ts)$/.test(entry.name)) {
          const src = readFileSync(p, "utf8");
          if (/#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\b/.test(src)) offenders.push(`${entry.name}: raw hex`);
          if (/['"`][^'"`]*\b\d+px\b[^'"`]*['"`]/.test(src)) offenders.push(`${entry.name}: bare px literal`);
        }
      }
    };
    walk(uiDir);
    expect(offenders).toEqual([]);
  });
});

describe("R6 · dependencies are allow-listed", () => {
  it("package.json deps + devDeps are a subset of the frozen allow-list", () => {
    const allow = readJson(resolve(DASH, "tests/design-system/allowlist.json"));
    const pkg = readJson(resolve(DASH, "package.json"));
    const allowed = new Set([
      ...(allow.dependencies as string[]),
      ...(allow.devDependencies as string[]),
    ]);
    const present = [
      ...Object.keys((pkg.dependencies as Record<string, string>) ?? {}),
      ...Object.keys((pkg.devDependencies as Record<string, string>) ?? {}),
    ];
    const stray = present.filter((d) => !allowed.has(d));
    expect(stray).toEqual([]);
  });
});
