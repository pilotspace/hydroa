/**
 * landing-fidelity (ui-fidelity milestone) — RED before build.
 *
 * Asserts the Aurora COMPOSITION lands on the marketing + auth surfaces while the
 * frozen landing structure (one h1, #product/#pricing/#docs sections, links) and the
 * AuthShell decorative-panel a11y contract stay intact. The full landing-page +
 * auth suites remain the behavioural regression guard.
 */
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import MarketingRootPage from "@/app/(marketing)/page";
import { AuthShell } from "@/components/ui/auth-shell";

describe("landing-fidelity · hero Aurora treatment", () => {
  it("gives the hero a decorative aurora background + a gradient-clipped headline", () => {
    const { container } = render(<MarketingRootPage />);
    // a decorative (aria-hidden) aurora background layer behind the hero
    const aurora = container.querySelector('[data-slot="hero-aurora"]');
    expect(aurora).not.toBeNull();
    expect(aurora!.getAttribute("aria-hidden")).toBe("true");
    // a gradient-clipped accent span inside the single h1
    const h1 = container.querySelector("h1");
    expect(h1).not.toBeNull();
    const grad = h1!.querySelector(".bg-clip-text");
    expect(grad).not.toBeNull();
    expect(grad!.className).toContain("from-brand-from");
  });

  it("preserves the frozen structure: one h1 + ordered #product/#pricing/#docs", () => {
    const { container } = render(<MarketingRootPage />);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    for (const id of ["product", "pricing", "docs"]) {
      expect(container.querySelector(`section#${id}`)).not.toBeNull();
    }
    // the hero CTAs still link out
    const hrefs = Array.from(container.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(expect.arrayContaining(["/signup", "/login", "/pricing", "/docs"]));
  });
});

describe("landing-fidelity · final CTA brand gradient", () => {
  it("renders the final CTA on the brand gradient surface", () => {
    const { container } = render(<MarketingRootPage />);
    const cta = container.querySelector('section[aria-labelledby="cta-heading"]');
    expect(cta).not.toBeNull();
    expect(cta!.className).toContain("from-brand-from");
    expect(cta!.className).toContain("to-brand-to");
  });
});

describe("landing-fidelity · auth brand panel", () => {
  it("elevates the decorative brand panel to the brand gradient, still aria-hidden", () => {
    const { container } = render(
      <AuthShell>
        <h1>Sign in</h1>
      </AuthShell>,
    );
    const panel = container.querySelector('[data-slot="auth-brand"]');
    expect(panel).not.toBeNull();
    expect(panel!.getAttribute("aria-hidden")).toBe("true");
    expect(panel!.className).toContain("from-brand-from");
    // the panel carries no heading/landmark/focusable child (decorative)
    expect(panel!.querySelector("h1,h2,h3,a,button,input")).toBeNull();
    // exactly one main + one h1 survive
    expect(container.querySelectorAll("main")).toHaveLength(1);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
  });
});
