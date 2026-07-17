/**
 * docs-quickstart-page.test.tsx — RED suite for activation-quickstart TASK.md §3 v1
 * #7/#8, M8 + M9 + R2 (docs index href change + new public /docs/quickstart page).
 *
 * RED before Build: app/(marketing)/docs/quickstart/page.tsx does not exist yet;
 * docs/page.tsx's CATEGORIES has no href for "Quickstart" yet (falls through to
 * "#coming-soon"). New file — does not edit tests/docs-blog.test.tsx.
 */
import { describe, it, expect, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { axe } from "@/test-support/axe";
import DocsPage from "@/app/(marketing)/docs/page";
import DocsQuickstartPage from "@/app/(marketing)/docs/quickstart/page";

const ROOT = resolve(__dirname, "..");
const readSrc = (rel: string) =>
  existsSync(resolve(ROOT, rel)) ? readFileSync(resolve(ROOT, rel), "utf-8") : "";

const ORIGINAL_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL;
afterEach(() => {
  if (ORIGINAL_BASE_URL === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL;
  else process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_BASE_URL;
});

// ── M8: docs index link resolves ─────────────────────────────────────────────
describe("test_docs_quickstart_link_resolves", () => {
  it("'Quickstart' category links to /docs/quickstart, not #coming-soon", () => {
    render(<DocsPage />);
    const links = screen.getAllByRole("link");
    const quickstart = links.find((l) => l.getAttribute("href") === "/docs/quickstart");
    expect(quickstart).toBeTruthy();
  });

  it("the other 3 stub categories still link to #coming-soon, unchanged", () => {
    render(<DocsPage />);
    const comingSoon = screen.getAllByRole("link").filter((l) => l.getAttribute("href") === "#coming-soon");
    expect(comingSoon.length).toBe(3);
  });
});

// ── M9/R2: public quickstart page never leaks a real secret ─────────────────
describe("test_docs_quickstart_no_real_secret_no_credentialed_fetch", () => {
  it("renders the fixed placeholder key, never a real one", () => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.hydroa.dev";
    render(<DocsQuickstartPage />);
    expect(screen.getAllByText(/sk-your-api-key/).length).toBeGreaterThan(0);
  });

  it("page source has no cookies()/next-headers import and no client fetch", () => {
    const src = readSrc("app/(marketing)/docs/quickstart/page.tsx");
    expect(src).not.toBe("");
    expect(src).not.toMatch(/cookies\(\)/);
    expect(src).not.toMatch(/from ["']next\/headers["']/);
    expect(src).not.toMatch(/bffGet|api-client|useQuery|fetch\(/);
  });

  it("has no 'use client' directive on the page itself", () => {
    const src = readSrc("app/(marketing)/docs/quickstart/page.tsx");
    expect(src.trimStart().startsWith('"use client"')).toBe(false);
  });

  it("reuses the same QuickstartPanel component M4 introduces (never duplicated)", () => {
    const src = readSrc("app/(marketing)/docs/quickstart/page.tsx");
    expect(src).toMatch(/QuickstartPanel/);
  });
});

// ── shared a11y bar ───────────────────────────────────────────────────────────
describe("test_docs_quickstart_a11y", () => {
  it("axe reports 0 serious/critical violations", async () => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.hydroa.dev";
    const { container } = render(<DocsQuickstartPage />);
    const results = await axe(container);
    const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(serious).toEqual([]);
  });

  it("exactly one h1", () => {
    const { container } = render(<DocsQuickstartPage />);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
  });
});
