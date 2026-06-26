/**
 * tests/motion-primitives.test.tsx — v50 motion-primitives
 *
 * Progressive, reduced-motion-safe motion. The a11y guarantee is the global
 * globals.css net (reduced-motion users get no animation); Reveal is the
 * motion-safe entrance that ALWAYS renders its content.
 *
 * RED before build: components/ui/motion.tsx does not exist → MODULE_NOT_FOUND;
 * globals.css has no reduced-motion block.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen } from "@testing-library/react";

import { Reveal } from "@/components/ui/motion";
import { Reveal as RevealFromBarrel } from "@/components/ui";

describe("Reveal", () => {
  it("test_reveal_renders_children_and_motion_safe", () => {
    render(<Reveal>hello-content</Reveal>);
    const child = screen.getByText("hello-content");
    expect(child).toBeInTheDocument();
    expect(child.className).toMatch(/motion-safe:/);
  });

  it("test_reveal_merges_props", () => {
    render(
      <Reveal as="section" className="custom" data-testid="r">
        child
      </Reveal>,
    );
    const el = screen.getByTestId("r");
    expect(el.tagName).toBe("SECTION");
    expect(el.className).toContain("custom");
    expect(el.className).toMatch(/motion-safe:/);
  });

  it("test_reveal_delay_applies_motion_safe_delay", () => {
    render(
      <Reveal delay={150} data-testid="d">
        child
      </Reveal>,
    );
    expect(screen.getByTestId("d").className).toContain("motion-safe:delay-150");
  });

  it("test_barrel_exports_reveal", () => {
    expect(typeof RevealFromBarrel).toBe("function");
  });
});

describe("globals.css reduced-motion net", () => {
  it("test_globals_has_reduced_motion_net", () => {
    const css = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css).toContain("animation-duration");
    expect(css).toContain("transition-duration");
  });
});
