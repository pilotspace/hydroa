/**
 * legal-pages.test.tsx — suite for the v38 public legal routes (terms/privacy/security).
 * Behavioral render + axe + a source guard for the public-route invariant, plus a
 * footer-link-resolution check against the FROZEN MarketingShell footer.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { axe } from "@/test-support/axe";
import TermsPage from "@/app/(marketing)/legal/terms/page";
import PrivacyPage from "@/app/(marketing)/legal/privacy/page";
import SecurityPage from "@/app/(marketing)/legal/security/page";

const ROOT = resolve(__dirname, "..");
const readSrc = (rel: string) =>
  existsSync(resolve(ROOT, rel)) ? readFileSync(resolve(ROOT, rel), "utf-8") : "";

const PAGES = [
  { name: "terms", Comp: TermsPage, src: "app/(marketing)/legal/terms/page.tsx" },
  { name: "privacy", Comp: PrivacyPage, src: "app/(marketing)/legal/privacy/page.tsx" },
  { name: "security", Comp: SecurityPage, src: "app/(marketing)/legal/security/page.tsx" },
];

describe("test_each_legal_renders", () => {
  it.each(PAGES)("$name has one h1, a Last updated date, and >=2 h2", ({ Comp }) => {
    const { container } = render(<Comp />);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(screen.getByText(/last updated/i)).toBeInTheDocument();
    expect(container.querySelectorAll("h2").length).toBeGreaterThanOrEqual(2);
  });
});

describe("test_footer_links_resolve", () => {
  it("every shell footer /legal/* link has a matching page module", () => {
    const shell = readSrc("components/marketing-shell.tsx");
    const legalHrefs = Array.from(shell.matchAll(/href:\s*["'](\/legal\/[a-z]+)["']/g)).map(
      (m) => m[1],
    );
    expect(legalHrefs.length).toBeGreaterThanOrEqual(3);
    for (const href of legalHrefs) {
      const slug = href.replace("/legal/", "");
      expect(existsSync(resolve(ROOT, `app/(marketing)/legal/${slug}/page.tsx`))).toBe(true);
    }
  });
});

describe("test_template_notice", () => {
  it.each(PAGES)("$name shows the template/placeholder notice", ({ Comp }) => {
    render(<Comp />);
    expect(screen.getByText(/template notice/i)).toBeInTheDocument();
    expect(screen.getByText(/not legal advice/i)).toBeInTheDocument();
  });
});

describe("test_legal_a11y", () => {
  it("axe reports 0 serious/critical on a legal page", async () => {
    const { container } = render(<PrivacyPage />);
    const results = await axe(container);
    expect(
      results.violations.filter((v) => v.impact === "serious" || v.impact === "critical"),
    ).toEqual([]);
  });
});

describe("test_reject_heading_order", () => {
  it.each(PAGES)("$name has exactly one h1 and no skipped level", ({ Comp }) => {
    const { container } = render(<Comp />);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    const levels = Array.from(container.querySelectorAll("h1,h2,h3,h4,h5,h6")).map(
      (h) => Number(h.tagName[1]),
    );
    for (let i = 1; i < levels.length; i++) {
      expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
    }
  });
});

describe("test_reject_public_not_gated", () => {
  it.each(PAGES)("$name source reads no cookies() / no authed fetch", ({ src }) => {
    const s = readSrc(src);
    expect(s).not.toBe("");
    expect(s).not.toMatch(/cookies\(\)/);
    expect(s).not.toMatch(/from ["']next\/headers["']/);
    expect(s).not.toMatch(/bffGet|api-client|useQuery/);
  });
});
