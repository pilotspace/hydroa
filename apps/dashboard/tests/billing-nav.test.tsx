/**
 * tests/billing-nav.test.tsx — RED suite for the Billing nav group
 * (billing-ui TASK.md §3 — FROZEN @ v1, M1/R1/R2).
 *
 * Renders the real AppShell (mirrors tests/design-system/app-shell-sidebar.test.tsx's own
 * render pattern) and asserts the RBAC-visibility matrix from DESIGN.md §2: "Invoices" is a
 * denylist item (minRole:"admin", hidden from member only); "Credits"/"Plan & seats" carry no
 * minRole (visible to every role, including operator/viewer, who still 403 server-side).
 *
 * One test per §2 nav scenario (3) + a data-level NAV_GROUPS shape check.
 */

import { describe, expect, it, vi, beforeAll, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";

vi.mock("next/navigation", () => ({
  usePathname: vi.fn(() => "/"),
  useRouter: vi.fn(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() })),
}));

import { AppShell, NAV_GROUPS } from "@/components/ui/app-shell";

const Body = () => (
  <>
    <h1>Page</h1>
  </>
);

beforeAll(() => {
  if (!("ResizeObserver" in globalThis)) {
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => true,
    }),
  });
});

beforeEach(() => {
  localStorage.clear();
});

describe("NAV_GROUPS — Billing group shape (M1)", () => {
  it("test_billing_group_sits_between_insights_and_configure_with_the_right_gating", () => {
    const labels = NAV_GROUPS.map((g) => g.label);
    const insightsIdx = labels.indexOf("Insights");
    const billingIdx = labels.indexOf("Billing");
    const configureIdx = labels.indexOf("Configure");
    expect(billingIdx).toBe(insightsIdx + 1);
    expect(configureIdx).toBe(billingIdx + 1);

    const billing = NAV_GROUPS[billingIdx];
    expect(billing.items.map((i) => i.label)).toEqual(["Invoices", "Credits", "Plan & seats"]);

    const invoices = billing.items.find((i) => i.label === "Invoices");
    const credits = billing.items.find((i) => i.label === "Credits");
    const plan = billing.items.find((i) => i.label === "Plan & seats");
    expect(invoices?.href).toBe("/app/invoices");
    expect(invoices?.minRole).toBe("admin");
    expect(credits?.href).toBe("/app/credits");
    expect(credits?.minRole).toBeUndefined();
    expect(plan?.href).toBe("/app/plan");
    expect(plan?.minRole).toBeUndefined();
  });

  it("test_zero_changes_to_the_26_pre_existing_nav_items_hrefs", () => {
    const preExistingHrefs = [
      "/app/chat",
      "/app/voice",
      "/app/memory",
      "/app/artifacts",
      "/app/vision",
      "/app/video",
      "/app/usage",
      "/app/spend",
      "/app/keys",
      "/app/presets",
      "/app/models",
      "/app/routing",
      "/app/batches",
      "/app/teams",
      "/app/members",
      "/app/alerts",
      "/app/audit",
      "/app/logs",
      "/app/health",
      "/app/slo",
      "/app/guardrail-analytics",
    ];
    const allHrefs = NAV_GROUPS.flatMap((g) => g.items.map((i) => i.href));
    for (const href of preExistingHrefs) {
      expect(allHrefs).toContain(href);
    }
  });
});

describe("AppShell — Billing nav visibility by role (M1, R1, R2)", () => {
  it("test_owner_sees_all_three_billing_nav_items", () => {
    render(
      <AppShell role="owner">
        <Body />
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: /^Invoices$/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Credits$/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Plan & seats$/i })).toBeInTheDocument();
  });

  it("test_members_nav_hides_invoices_but_keeps_credits_and_plan_seats", () => {
    render(
      <AppShell role="member">
        <Body />
      </AppShell>,
    );
    expect(screen.queryByRole("link", { name: /^Invoices$/i })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Credits$/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Plan & seats$/i })).toBeInTheDocument();
  });

  it("test_operators_nav_still_shows_invoices_fail_open", () => {
    render(
      <AppShell role="operator">
        <Body />
      </AppShell>,
    );
    // fail-open UX-only nav — the gateway is the real gate (R2 handles the 403 on click)
    expect(screen.getByRole("link", { name: /^Invoices$/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Credits$/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Plan & seats$/i })).toBeInTheDocument();
  });
});
