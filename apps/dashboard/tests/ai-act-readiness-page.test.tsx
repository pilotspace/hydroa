/**
 * ai-act-readiness-page.test.tsx — RED suite for the ai-act-marketing-page
 * task (frozen contract v1). Covers the §2 scenarios for the public
 * /ai-act-readiness page: M1-M13 (minus M9/M10 which live in their own
 * sibling suites) + R1-R7 + the two edge cases, following the
 * render+role-query+axe+source-guard idiom of pricing-page.test.tsx.
 *
 * RED before build: app/(marketing)/ai-act-readiness/page.tsx does not exist.
 */
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { axe } from "@/test-support/axe";
import AiActReadinessPage from "@/app/(marketing)/ai-act-readiness/page";
import { MarketingShell } from "@/components/marketing-shell";

const DASHBOARD_ROOT = resolve(__dirname, "..");
function readSource(rel: string): string {
  const abs = resolve(DASHBOARD_ROOT, rel);
  return existsSync(abs) ? readFileSync(abs, "utf-8") : "";
}

// ── M1: public route, no auth surface ──────────────────────────────────────
describe("test_public_route_renders_no_auth_surface", () => {
  it("renders with a single main#main and exactly one h1", () => {
    const { container } = render(<AiActReadinessPage />);
    expect(container.querySelectorAll("main#main")).toHaveLength(1);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("page source reads no cookies() and does no authenticated fetch", () => {
    const src = readSource("app/(marketing)/ai-act-readiness/page.tsx");
    expect(src).not.toBe("");
    expect(src).not.toMatch(/cookies\(\)/);
    expect(src).not.toMatch(/from ["']next\/headers["']/);
    expect(src).not.toMatch(/bffGet|api-client|useQuery|fetch\(/);
  });
});

// ── M2 / R1 / R3: Art.101 figures cited, Art.99 never leaks ────────────────
describe("test_art101_figures_carry_citation", () => {
  it("3% appears in the same visible text node as Art. 101", () => {
    render(<AiActReadinessPage />);
    expect(screen.getByText(/3%.*Art\.\s*101/)).toBeInTheDocument();
  });

  it("€15M appears in the same visible text node as Art. 101", () => {
    render(<AiActReadinessPage />);
    expect(screen.getByText(/€15M.*Art\.\s*101/)).toBeInTheDocument();
  });

  it("the Aug 2, 2026 enforcement date appears in the same visible text node as Art. 101", () => {
    render(<AiActReadinessPage />);
    expect(screen.getByText(/Aug 2, 2026.*Art\.\s*101/)).toBeInTheDocument();
  });

  it("never renders the Art.99 general-infringement figure (reject R1/ART99_FIGURE_LEAKED)", () => {
    const { container } = render(<AiActReadinessPage />);
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/€35M/);
    expect(text).not.toMatch(/35,000,000/);
    expect(text).not.toMatch(/7%\s*(of\s*)?(global\s*)?(annual\s*)?turnover/i);
  });

  it("every occurrence of 3% / €15M is immediately paired with Art. 101 (reject R3/UNCITED_LEGAL_FIGURE)", () => {
    const { container } = render(<AiActReadinessPage />);
    const text = container.textContent ?? "";
    const bare3pct = text.match(/3%(?!\s*\(Art\.\s*101\))/g) ?? [];
    const bare15m = text.match(/€15M(?!\s*\(Art\.\s*101\))/g) ?? [];
    expect(bare3pct).toEqual([]);
    expect(bare15m).toEqual([]);
  });
});

// ── edge case: "whichever higher" wording unambiguous, within the pairing ──
describe("test_whichever_higher_wording_unambiguous", () => {
  it("states 'whichever is higher' directly alongside the 3%/€15M tile pairing", () => {
    const { container } = render(<AiActReadinessPage />);
    const text = container.textContent ?? "";
    expect(text).toMatch(/whichever is higher/i);
  });
});

// ── M3 / R2: no compliance-claim overreach ─────────────────────────────────
describe("test_no_compliance_claim_overreach", () => {
  it("never states AI Act compliant / makes you compliant / GPAI compliance", () => {
    const { container } = render(<AiActReadinessPage />);
    const text = (container.textContent ?? "").toLowerCase();
    expect(text).not.toMatch(/ai act compliant/);
    expect(text).not.toMatch(/makes (you|it) compliant/);
    expect(text).not.toMatch(/gpai compliance/);
  });

  it("uses record-keeping/audit-readiness-support framing instead", () => {
    const { container } = render(<AiActReadinessPage />);
    const text = container.textContent ?? "";
    expect(text).toMatch(/(record-keeping|audit-readiness) support/i);
  });
});

// ── M4: residency refuse-not-reroute ────────────────────────────────────────
describe("test_residency_refuse_not_reroute", () => {
  it("states the request is refused, never silently rerouted", () => {
    render(<AiActReadinessPage />);
    expect(screen.getByText(/refused, never silently rerouted/i)).toBeInTheDocument();
  });
});

// ── M4a / R4: honest /app/settings link, no deep-link claim ────────────────
describe("test_residency_settings_link_is_honest", () => {
  it("links to /app/settings naming the exact 'Data & residency' tab label", () => {
    render(<AiActReadinessPage />);
    const link = screen.getByRole("link", { name: /data & residency/i });
    expect(link.getAttribute("href")).toBe("/app/settings");
  });

  it("does not claim the link lands pre-selected on that tab (reject R4/DANGLING_CONSOLE_LINK)", () => {
    const { container } = render(<AiActReadinessPage />);
    const text = (container.textContent ?? "").toLowerCase();
    expect(text).not.toMatch(/pre-selected/);
    expect(text).not.toMatch(/takes? you (directly|straight) to/);
    expect(text).not.toMatch(/opens (directly|straight) (on|to)/);
  });

  it("never links to the not-yet-shipped compliance-report-center console extension", () => {
    render(<AiActReadinessPage />);
    const links = screen.getAllByRole("link");
    for (const l of links) {
      expect(l.getAttribute("href") ?? "").not.toMatch(/compliance-report-center/);
    }
  });
});

// ── M5: ZDR opt-in, confirm-gated, irreversible ────────────────────────────
describe("test_zdr_opt_in_and_irreversible", () => {
  it("states ZDR is opt-in and irreversible once enabled", () => {
    render(<AiActReadinessPage />);
    expect(screen.getByText(/opt-in/i)).toBeInTheDocument();
    expect(screen.getByText(/irreversible/i)).toBeInTheDocument();
  });

  it("never claims data is unseen by default absent the opt-in", () => {
    const { container } = render(<AiActReadinessPage />);
    const text = (container.textContent ?? "").toLowerCase();
    expect(text).not.toMatch(/we never see your data/);
    expect(text).not.toMatch(/unseen by default/);
  });
});

// ── M6 / R6: Art.12 bundle described in outcome terms only ─────────────────
describe("test_art12_bundle_outcome_only", () => {
  it("describes a dated, Art. 12-mapped record-keeping export as an outcome", () => {
    render(<AiActReadinessPage />);
    expect(screen.getByText(/dated, Art\. 12-mapped record-keeping export/i)).toBeInTheDocument();
  });

  it("names no specific field/section/manifest shape (reject R6/BUNDLE_SHAPE_PREEMPTED)", () => {
    const { container } = render(<AiActReadinessPage />);
    const text = (container.textContent ?? "").toLowerCase();
    expect(text).not.toMatch(/manifest/);
    expect(text).not.toMatch(/json schema/);
  });
});

// ── M7 / R5: vendor-risk figures accurate ──────────────────────────────────
describe("test_vendor_risk_figures_accurate", () => {
  it("names the Claude Fable 5 suspension window Jun 12-30, 2026", () => {
    const { container } = render(<AiActReadinessPage />);
    const text = container.textContent ?? "";
    expect(text).toMatch(/Fable 5/i);
    expect(text).toMatch(/Jun 12/);
    expect(text).toMatch(/30, 2026/);
    expect(text.toLowerCase()).toMatch(/suspend/);
  });

  it("states inference_geo accepts only us|global with 1.1x US-pin and +10% hyperscaler-regional", () => {
    const { container } = render(<AiActReadinessPage />);
    const text = container.textContent ?? "";
    expect(text).toMatch(/inference_geo/);
    expect(text).toMatch(/1\.1x|1\.1×/);
    expect(text).toMatch(/\+10%/);
  });

  it("never claims Anthropic offers first-party EU inference (reject R5/INACCURATE_VENDOR_CLAIM)", () => {
    const { container } = render(<AiActReadinessPage />);
    const text = (container.textContent ?? "").toLowerCase();
    expect(text).not.toMatch(/(offers|provides|has)\s+first-party eu inference/);
  });
});

// ── M8: exactly one primary + one secondary CTA, settings link permitted ───
describe("test_exactly_one_primary_one_secondary_cta", () => {
  it("exactly one primary CTA links to /signup", () => {
    render(<AiActReadinessPage />);
    const links = screen.getAllByRole("link").filter((l) => l.getAttribute("href") === "/signup");
    expect(links).toHaveLength(1);
  });

  it("exactly one secondary CTA targets /docs/ai-act-compliance", () => {
    render(<AiActReadinessPage />);
    const links = screen
      .getAllByRole("link")
      .filter((l) => l.getAttribute("href") === "/docs/ai-act-compliance");
    expect(links).toHaveLength(1);
  });

  it("the /app/settings reference is not counted as a third CTA (at most one, permitted)", () => {
    render(<AiActReadinessPage />);
    const links = screen
      .getAllByRole("link")
      .filter((l) => l.getAttribute("href") === "/app/settings");
    expect(links.length).toBeLessThanOrEqual(1);
  });
});

// ── M12: axe + heading discipline ───────────────────────────────────────────
describe("test_ai_act_readiness_a11y", () => {
  it("axe reports 0 serious/critical violations", async () => {
    const { container } = render(<AiActReadinessPage />);
    const results = await axe(container);
    const serious = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(serious).toEqual([]);
  });

  it("has exactly one h1 and no skipped heading level", () => {
    const { container } = render(<AiActReadinessPage />);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    const levels = Array.from(container.querySelectorAll("h1,h2,h3,h4,h5,h6")).map(
      (h) => Number(h.tagName[1]),
    );
    for (let i = 1; i < levels.length; i++) {
      expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
    }
  });
});

// ── M13 / R7: static stat-strip, no client countdown ───────────────────────
describe("test_stat_strip_is_static_not_a_countdown", () => {
  it("source has no 'use client' directive and no runtime Date computation", () => {
    const src = readSource("app/(marketing)/ai-act-readiness/page.tsx");
    expect(src).not.toBe("");
    expect(src).not.toMatch(/["']use client["']/);
    expect(src).not.toMatch(/new Date\(/);
    expect(src).not.toMatch(/Date\.now\(/);
  });

  it("renders the enforcement date as a fixed string, not a computed days-remaining value", () => {
    render(<AiActReadinessPage />);
    expect(screen.getByText(/Aug 2, 2026.*Art\.\s*101/)).toBeInTheDocument();
    expect(screen.queryByText(/days? (remaining|left|until)/i)).not.toBeInTheDocument();
  });
});

// ── M11: MarketingShell exposes one disclosed footer link, no nav widening ──
describe("test_marketing_shell_footer_link", () => {
  it("footer carries exactly one new link to /ai-act-readiness, nav is not widened", () => {
    const { container } = render(
      <MarketingShell>
        <main id="main">content</main>
      </MarketingShell>,
    );
    const nav = screen.getByRole("navigation", { name: /main/i });
    const navLinks = within(nav).getAllByRole("link");
    expect(navLinks.some((l) => l.getAttribute("href") === "/ai-act-readiness")).toBe(false);

    const footer = container.querySelector("footer");
    expect(footer).not.toBeNull();
    const footerLinks = within(footer as HTMLElement).getAllByRole("link");
    const aiActLinks = footerLinks.filter((l) => l.getAttribute("href") === "/ai-act-readiness");
    expect(aiActLinks).toHaveLength(1);
  });
});
