/**
 * tests/fuzzy.test.ts — RED for the Fuse.js-backed fuzzy matcher (v54 · #2).
 *
 * `lib/fuzzy.ts:fuzzySearch<T>(items, query, keys)` is the single seam that imports
 * fuse.js (Tin overrode the no-new-dep default at the contract freeze). It returns a
 * ranked best-first subset; an empty/whitespace query returns the items unchanged.
 *
 * RED before build: `@/lib/fuzzy` does not exist → MODULE_NOT_FOUND at collect.
 */
import { describe, it, expect } from "vitest";
import { fuzzySearch } from "@/lib/fuzzy";

interface M {
  id: string;
  name: string;
}

const ITEMS: M[] = [
  { id: "anthropic/claude-3-5-sonnet", name: "Claude 3.5 Sonnet" },
  { id: "openai/gpt-4o", name: "GPT-4o" },
  { id: "openai/gpt-4o-mini", name: "GPT-4o mini" },
  { id: "google/gemini-2.5-flash", name: "Gemini 2.5 Flash" },
];

const KEYS: (keyof M)[] = ["name", "id"];

describe("fuzzySearch", () => {
  it("returns items unchanged for an empty or whitespace query", () => {
    expect(fuzzySearch(ITEMS, "", KEYS)).toEqual(ITEMS);
    expect(fuzzySearch(ITEMS, "   ", KEYS)).toEqual(ITEMS);
  });

  it("returns [] when nothing matches", () => {
    expect(fuzzySearch(ITEMS, "zzzznotamodel", KEYS)).toEqual([]);
  });

  it("matches by name and ranks the best match first", () => {
    const out = fuzzySearch(ITEMS, "claude", KEYS);
    expect(out.length).toBeGreaterThanOrEqual(1);
    expect(out[0].name).toBe("Claude 3.5 Sonnet");
    // an unrelated model is excluded
    expect(out.some((m) => m.name === "Gemini 2.5 Flash")).toBe(false);
  });

  it("matches by id (the slug field), not just the display name", () => {
    const out = fuzzySearch(ITEMS, "gemini-2.5-flash", KEYS);
    expect(out.some((m) => m.id === "google/gemini-2.5-flash")).toBe(true);
  });

  it("tolerates a typo / transposition (Fuse.js)", () => {
    // "sonet" is a typo of "sonnet" — a pure substring filter would miss it.
    const out = fuzzySearch(ITEMS, "sonet", KEYS);
    expect(out.some((m) => m.name === "Claude 3.5 Sonnet")).toBe(true);
  });

  it("is case-insensitive", () => {
    const out = fuzzySearch(ITEMS, "GEMINI", KEYS);
    expect(out.some((m) => m.id === "google/gemini-2.5-flash")).toBe(true);
  });
});
