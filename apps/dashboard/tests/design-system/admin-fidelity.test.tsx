/**
 * admin-fidelity (ui-fidelity milestone) — RED before build.
 *
 * Asserts the Aurora KPI + admin-canvas uplift on the two shared primitives every
 * (app) surface inherits — StatCard + AppShell — while the frozen StatCard a11y hooks
 * and the v13 shell landmarks stay intact. The console-surfaces + enterprise-ext +
 * shell suites remain the behavioural regression guard.
 */
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { StatCard } from "@/components/ui/stat-card";
import { AppShell } from "@/components/ui/app-shell";

describe("admin-fidelity · elevated KPI", () => {
  it("renders the value at title scale with an uppercase tracked label, a11y intact", () => {
    const { container, getByText } = render(
      <StatCard
        label="Total spend"
        value="$1,234"
        valueTestId="totals-spend"
        delta={{ direction: "down", text: "12%" }}
      />,
    );
    // frozen hooks intact
    const card = container.querySelector('[data-slot="stat-card"]');
    expect(card).not.toBeNull();
    const valueNode = container.querySelector('[data-testid="totals-spend"]');
    expect(valueNode).not.toBeNull();
    // value elevated to title scale
    expect(valueNode!.className).toContain("text-3xl");
    // label is an uppercase tracked caption
    const label = getByText("Total spend");
    expect(label.className).toContain("uppercase");
    expect(label.className).toMatch(/tracking-/);
    // non-color-only trend preserved (sr-only word survives)
    expect(getByText("decrease")).toBeTruthy();
  });
});

describe("admin-fidelity · admin canvas", () => {
  it("gives the shell main a white-luxury canvas while keeping the frozen v13 landmarks", () => {
    const { container } = render(
      <AppShell activePath="/app/usage">
        <h1>Usage</h1>
      </AppShell>,
    );
    const main = container.querySelector("main#main");
    expect(main).not.toBeNull();
    // v7 premium reconciliation (Tin 2026-07-06): the canvas moved from the tinted
    // bg-muted/30 to the pure-white bg-background "luxury canvas" against the dark nav
    // rail. This pins the NEW confirmed surface; the v13 landmark guards below are unchanged.
    expect(main!.className).toMatch(/bg-background/);
    // skip-link is the first focusable element
    const firstLink = container.querySelector("a");
    expect(firstLink!.getAttribute("href")).toBe("#main");
    // exactly one Primary nav landmark in the default (closed-sheet) render
    const primaryNavs = Array.from(container.querySelectorAll("nav")).filter(
      (n) => n.getAttribute("aria-label") === "Primary",
    );
    expect(primaryNavs).toHaveLength(1);
  });
});
