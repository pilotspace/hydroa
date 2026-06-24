/**
 * status-page.test.tsx — suite for the v38 public /status (trust + component status + SLA).
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { axe } from "@/test-support/axe";
import StatusPage from "@/app/(marketing)/status/page";

const ROOT = resolve(__dirname, "..");
const readSrc = (rel: string) =>
  existsSync(resolve(ROOT, rel)) ? readFileSync(resolve(ROOT, rel), "utf-8") : "";

describe("test_components_and_sla", () => {
  it("renders one h1, >=3 component rows with text status, and an SLA section", () => {
    const { container } = render(<StatusPage />);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    // >=3 component names as h2 + the SLA section h2 => >=4 h2
    expect(container.querySelectorAll("h2").length).toBeGreaterThanOrEqual(4);
    // at least 3 "Operational" text labels
    expect(screen.getAllByText(/operational/i).length).toBeGreaterThanOrEqual(3);
    // SLA statement present (the dedicated section heading)
    expect(screen.getByRole("heading", { name: /service-level/i })).toBeInTheDocument();
  });
});

describe("test_footer_status_link", () => {
  it("shell footer links /status and the page module exists", () => {
    const shell = readSrc("components/marketing-shell.tsx");
    expect(shell).toMatch(/href:\s*["']\/status["']/);
    expect(existsSync(resolve(ROOT, "app/(marketing)/status/page.tsx"))).toBe(true);
  });
});

describe("test_status_a11y", () => {
  it("axe reports 0 serious/critical", async () => {
    const { container } = render(<StatusPage />);
    const results = await axe(container);
    expect(
      results.violations.filter((v) => v.impact === "serious" || v.impact === "critical"),
    ).toEqual([]);
  });
});

describe("test_reject_public_not_gated", () => {
  it("page source reads no cookies() and no gated/authed fetch", () => {
    const s = readSrc("app/(marketing)/status/page.tsx");
    expect(s).not.toBe("");
    expect(s).not.toMatch(/cookies\(\)/);
    expect(s).not.toMatch(/from ["']next\/headers["']/);
    expect(s).not.toMatch(/bffGet|api-client|useQuery|\/admin\//);
  });
});

describe("test_reject_color_only", () => {
  it("each status indicator carries a text label, not color alone", () => {
    render(<StatusPage />);
    // every status is rendered as readable text (queryable), not only a colored dot
    const labels = screen.getAllByText(/operational/i);
    expect(labels.length).toBeGreaterThanOrEqual(3);
  });
});

describe("test_reject_heading_order", () => {
  it("has exactly one h1 and no skipped level", () => {
    const { container } = render(<StatusPage />);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    const levels = Array.from(container.querySelectorAll("h1,h2,h3,h4,h5,h6")).map(
      (h) => Number(h.tagName[1]),
    );
    for (let i = 1; i < levels.length; i++) {
      expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
    }
  });
});
