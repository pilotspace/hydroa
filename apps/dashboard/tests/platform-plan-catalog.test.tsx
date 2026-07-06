/**
 * tests/platform-plan-catalog.test.tsx — RED suite for the plan-admin-ui task's
 * top-level, superadmin-only plan catalog page (Screen 1) + its "Plans" nav
 * entry (TASK.md M1, M2).
 *
 * GET /admin/platform/plans -> PlansListResponse { plans: PlanResponse[] }
 * (byte-identical to platform_plans_router.py's own frozen DTO, plan-catalog
 * §3 CONTRACT, confirmed by reading it directly). Null-ceiling wording
 * ("Unlimited") mirrors PlatformBudgetTab.tsx's own shipped convention exactly.
 *
 * RED before Build: `PlatformPlanCatalog` does not exist yet, and AppShell has
 * no "Plans" nav entry yet.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { PlatformPlanCatalog } from "@/components/platform/PlatformPlanCatalog";
import { AppShell } from "@/components/ui/app-shell";

const APP = "http://localhost:3000";

const STARTER = {
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  name: "starter",
  display_name: "Starter",
  seat_cap: 3,
  budget_usd_monthly_default: "50.00",
  rpm_limit_default: 60,
  tpm_limit_default: 40000,
};
const TEAM = {
  id: "aaaaaaaa-0000-0000-0000-000000000002",
  name: "team",
  display_name: "Team",
  seat_cap: null,
  budget_usd_monthly_default: "500.00",
  rpm_limit_default: 600,
  tpm_limit_default: 400000,
};
const ENTERPRISE = {
  id: "aaaaaaaa-0000-0000-0000-000000000003",
  name: "enterprise",
  display_name: "Enterprise",
  seat_cap: null,
  budget_usd_monthly_default: null,
  rpm_limit_default: null,
  tpm_limit_default: null,
};
const ALL_PLANS = [STARTER, TEAM, ENTERPRISE];

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderCatalog() {
  const client = makeQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <PlatformPlanCatalog />
    </QueryClientProvider>,
  );
}

describe("PlatformPlanCatalog", () => {
  it("test_catalog_lists_all_tiers_with_human_labeled_ceilings", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/plans`, () =>
        HttpResponse.json({ plans: ALL_PLANS }),
      ),
    );

    renderCatalog();

    await waitFor(() => {
      expect(screen.getByText("Starter")).toBeInTheDocument();
    });
    expect(screen.getByText("Team")).toBeInTheDocument();
    expect(screen.getByText("Enterprise")).toBeInTheDocument();

    // Starter: every ceiling is a concrete, human-labeled value.
    expect(screen.getByText("50.00")).toBeInTheDocument();
    expect(screen.getByText("60")).toBeInTheDocument();
    expect(screen.getByText("40000")).toBeInTheDocument();

    // Every null default (Team's seat_cap + Enterprise's all 4) renders the
    // single word "Unlimited" — never a bare/blank cell (mirrors
    // PlatformBudgetTab's own shipped convention). At least 4 instances
    // (Enterprise alone) — loose on purpose, not overfit to an exact count.
    expect(screen.getAllByText(/^unlimited$/i).length).toBeGreaterThanOrEqual(4);
  });

  it("test_catalog_shows_empty_state_when_no_tiers_exist", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/plans`, () => HttpResponse.json({ plans: [] })),
    );

    renderCatalog();

    await waitFor(() => {
      expect(screen.getByText(/no plan tiers configured/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("test_catalog_shows_error_state_on_fetch_failure", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/plans`, () =>
        HttpResponse.json({ title: "Internal server error", status: 500 }, { status: 500 }),
      ),
    );

    renderCatalog();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    });
    expect(screen.queryByText("Starter")).not.toBeInTheDocument();
  });

  it("test_catalog_shows_standard_error_state_on_403_non_superadmin", async () => {
    // R1: a non-superadmin (or unauthenticated) hit on this NEW surface renders
    // the SAME standard ErrorState every other existing admin page already
    // uses — no new client-side role gate, no special-cased copy for this
    // specific surface.
    server.use(
      http.get(`${APP}/api/gw/admin/platform/plans`, () =>
        HttpResponse.json(
          { code: "ERR_AUTH_FORBIDDEN", title: "Insufficient role for this action" },
          { status: 403 },
        ),
      ),
    );

    renderCatalog();

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/insufficient role/i);
    });
    expect(screen.queryByText("Starter")).not.toBeInTheDocument();
  });

  // console-flat-visual-pass, 2026-07-06 (M1) — PlanCard is shared by this Screen-1
  // catalog page and Screen 2's PlatformPlanTab; both go flat via the ONE shared
  // component (see Issues/Risks #4 — this page never passes `selected`, so no card
  // here carries the selected-border, only the base flat treatment).
  it("test_catalog_plan_cards_render_flat_variant", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/plans`, () =>
        HttpResponse.json({ plans: ALL_PLANS }),
      ),
    );
    const { container } = renderCatalog();
    await waitFor(() => {
      expect(screen.getByText("Starter")).toBeInTheDocument();
    });
    const flatCards = container.querySelectorAll('[data-variant="flat"]');
    expect(flatCards.length).toBe(3);
    flatCards.forEach((card) => {
      expect(card.className).toContain("rounded-[var(--radius-flat-card)]");
      expect(card.className).not.toContain("border-border");
      expect(card.className).not.toContain("border-primary");
    });
  });
});

function primaryNav() {
  return screen.getByRole("navigation", { name: /primary/i });
}

function mobileNav() {
  return screen.getByRole("navigation", { name: /site/i });
}

describe("AppShell — Plans nav entry (plan-admin-ui)", () => {
  it("test_plans_nav_visible_for_superadmin_desktop_and_mobile", () => {
    render(
      <AppShell role="superadmin">
        <div>content</div>
      </AppShell>,
    );

    const desktopLink = within(primaryNav()).getByRole("link", { name: /^plans$/i });
    expect(desktopLink).toBeInTheDocument();
    expect(desktopLink).toHaveAttribute("href", "/app/platform/plans");

    fireEvent.click(screen.getByRole("button", { name: /open navigation/i }));
    const mobileLink = within(mobileNav()).getByRole("link", { name: /^plans$/i });
    expect(mobileLink).toBeInTheDocument();
    expect(mobileLink).toHaveAttribute("href", "/app/platform/plans");
  });

  it("test_plans_nav_hidden_for_non_superadmin_roles", () => {
    const roles: Array<string | null | undefined> = [null, undefined, "member", "admin", "owner"];
    for (const role of roles) {
      const { unmount } = render(
        <AppShell role={role}>
          <div>content</div>
        </AppShell>,
      );
      expect(
        within(primaryNav()).queryByRole("link", { name: /^plans$/i }),
      ).not.toBeInTheDocument();
      unmount();
    }
  });
});
