/**
 * ai-act-compliance-docs-page.test.tsx — RED suite for the ai-act-marketing-page
 * task (frozen contract v1), covering /docs/ai-act-compliance (M9, R8) plus
 * the shared M12 a11y bar and the same copy-accuracy floor as the sibling
 * /ai-act-readiness page.
 *
 * RED before build: app/(marketing)/docs/ai-act-compliance/page.tsx does not
 * exist; docs/page.tsx's CATEGORIES has no real entry for it yet.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { axe } from "@/test-support/axe";
import AiActComplianceDocsPage from "@/app/(marketing)/docs/ai-act-compliance/page";
import DocsPage from "@/app/(marketing)/docs/page";

const DASHBOARD_ROOT = resolve(__dirname, "..");
function readSource(rel: string): string {
  const abs = resolve(DASHBOARD_ROOT, rel);
  return existsSync(abs) ? readFileSync(abs, "utf-8") : "";
}

// ── M9: real content, not a stub ───────────────────────────────────────────
describe("test_docs_page_carries_real_content", () => {
  it("renders with one h1 and substantive prose describing the Art.12 bundle's purpose/scope", () => {
    const { container } = render(<AiActComplianceDocsPage />);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    const text = container.textContent ?? "";
    expect(text.length).toBeGreaterThan(400);
    expect(text).toMatch(/Art\. 12/);
  });

  it("links back to /ai-act-readiness", () => {
    render(<AiActComplianceDocsPage />);
    const link = screen
      .getAllByRole("link")
      .find((l) => l.getAttribute("href") === "/ai-act-readiness");
    expect(link).toBeTruthy();
  });

  it("docs/page.tsx's CATEGORIES gains one real entry linking here (not #coming-soon)", () => {
    render(<DocsPage />);
    const links = screen.getAllByRole("link");
    const target = links.find((l) => l.getAttribute("href") === "/docs/ai-act-compliance");
    expect(target).toBeTruthy();
  });

  it("the remaining docs categories stay untouched 'coming soon' stubs", () => {
    // Superseded by activation-quickstart §3 M8 (FROZEN 2026-07-17): the
    // "Quickstart" entry gained a real href (/docs/quickstart) via the same
    // disclosed carve-out precedent this suite itself established — 4 → 3 stubs.
    render(<DocsPage />);
    const comingSoonLinks = screen
      .getAllByRole("link")
      .filter((l) => l.getAttribute("href") === "#coming-soon");
    expect(comingSoonLinks.length).toBe(3);
  });
});

// ── R8: not a stub ──────────────────────────────────────────────────────────
describe("test_reject_docs_stub_not_real_content", () => {
  it("does not render as a bare Coming-soon stub with no body prose", () => {
    render(<AiActComplianceDocsPage />);
    expect(screen.queryByText(/coming soon/i)).not.toBeInTheDocument();
  });
});

// ── shared copy-accuracy floor (defense in depth alongside the sibling page) ─
describe("test_docs_page_copy_accuracy_floor", () => {
  it("never states AI Act compliant / makes you compliant / GPAI compliance", () => {
    const { container } = render(<AiActComplianceDocsPage />);
    const text = (container.textContent ?? "").toLowerCase();
    expect(text).not.toMatch(/ai act compliant/);
    expect(text).not.toMatch(/makes (you|it) compliant/);
    expect(text).not.toMatch(/gpai compliance/);
  });

  it("never renders the Art.99 general-infringement figure", () => {
    const { container } = render(<AiActComplianceDocsPage />);
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/€35M/);
    expect(text).not.toMatch(/7%\s*(of\s*)?(global\s*)?(annual\s*)?turnover/i);
  });

  it("names no specific Art.12 bundle field/section/manifest shape", () => {
    const { container } = render(<AiActComplianceDocsPage />);
    const text = (container.textContent ?? "").toLowerCase();
    expect(text).not.toMatch(/manifest/);
    expect(text).not.toMatch(/json schema/);
  });
});

// ── M12: a11y ────────────────────────────────────────────────────────────────
describe("test_ai_act_compliance_docs_a11y", () => {
  it("axe reports 0 serious/critical violations", async () => {
    const { container } = render(<AiActComplianceDocsPage />);
    const results = await axe(container);
    const serious = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(serious).toEqual([]);
  });

  it("has exactly one h1 and no skipped heading level", () => {
    const { container } = render(<AiActComplianceDocsPage />);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    const levels = Array.from(container.querySelectorAll("h1,h2,h3,h4,h5,h6")).map(
      (h) => Number(h.tagName[1]),
    );
    for (let i = 1; i < levels.length; i++) {
      expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
    }
  });
});

// ── public/static source guard for this page too ────────────────────────────
describe("test_docs_page_public_and_static", () => {
  it("page source reads no cookies() and does no authenticated fetch", () => {
    const src = readSource("app/(marketing)/docs/ai-act-compliance/page.tsx");
    expect(src).not.toBe("");
    expect(src).not.toMatch(/cookies\(\)/);
    expect(src).not.toMatch(/from ["']next\/headers["']/);
    expect(src).not.toMatch(/bffGet|api-client|useQuery|fetch\(/);
  });

  it("has no 'use client' directive", () => {
    const src = readSource("app/(marketing)/docs/ai-act-compliance/page.tsx");
    expect(src).not.toMatch(/["']use client["']/);
  });
});
