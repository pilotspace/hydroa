/**
 * docs-blog.test.tsx — suite for the v38 /docs + /blog public scaffolds.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { axe } from "@/test-support/axe";
import DocsPage from "@/app/(marketing)/docs/page";
import BlogPage from "@/app/(marketing)/blog/page";

const ROOT = resolve(__dirname, "..");
const readSrc = (rel: string) =>
  existsSync(resolve(ROOT, rel)) ? readFileSync(resolve(ROOT, rel), "utf-8") : "";

describe("test_docs_categories", () => {
  it("renders one h1 and >=3 category cards", () => {
    const { container } = render(<DocsPage />);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(container.querySelectorAll("h2").length).toBeGreaterThanOrEqual(3);
  });
});

describe("test_blog_index", () => {
  it("renders one h1 and an empty-state or >=1 entry", () => {
    const { container } = render(<BlogPage />);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(screen.getByText(/no posts yet/i)).toBeInTheDocument();
  });
});

describe("test_docs_link_resolves", () => {
  it("the /docs page module exists on disk (landing link target)", () => {
    expect(existsSync(resolve(ROOT, "app/(marketing)/docs/page.tsx"))).toBe(true);
  });
});

describe("test_scaffold_a11y", () => {
  it("axe reports 0 serious/critical on /docs", async () => {
    const { container } = render(<DocsPage />);
    const results = await axe(container);
    expect(
      results.violations.filter((v) => v.impact === "serious" || v.impact === "critical"),
    ).toEqual([]);
  });
});

describe("test_reject_heading_order", () => {
  it.each([
    ["docs", DocsPage],
    ["blog", BlogPage],
  ] as const)("%s has exactly one h1 and no skipped level", (_name, Comp) => {
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
  it.each([
    "app/(marketing)/docs/page.tsx",
    "app/(marketing)/blog/page.tsx",
  ])("%s source reads no cookies() / no authed fetch", (src) => {
    const s = readSrc(src);
    expect(s).not.toBe("");
    expect(s).not.toMatch(/cookies\(\)/);
    expect(s).not.toMatch(/from ["']next\/headers["']/);
    expect(s).not.toMatch(/bffGet|api-client|useQuery/);
  });
});
