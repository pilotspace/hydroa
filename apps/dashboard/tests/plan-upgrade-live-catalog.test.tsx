/**
 * tests/plan-upgrade-live-catalog.test.tsx — RED-first suite for the live self-serve plans
 * catalog wiring (self-serve-plans-catalog TASK.md §3 — FROZEN @ v1, M4).
 *
 * RED before Build: `PlanSeatsPage` still hardcodes `upgradeOptions = []` and
 * `lib/checkout.ts` has no `fetchSelfServePlans` -> the dialog's menu never reflects
 * GET /admin/plans, the correct RED failure for this suite (an assertion failure, not an
 * import error — every symbol under test already exists pre-Build).
 *
 * Mirrors tests/billing-plan.test.tsx's QueryClientProvider wrapper + msw mocking style.
 */

import { describe, expect, it } from "vitest";
import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { PlanSeatsPage } from "@/components/plan/PlanSeatsPage";

const APP = "http://localhost:3000";
const PLAN_URL = `${APP}/api/gw/admin/plan`;
const BUDGET_URL = `${APP}/api/gw/admin/budget`;
const USERS_URL = `${APP}/api/gw/admin/users`;
const SELF_SERVE_PLANS_URL = `${APP}/api/gw/admin/plans`;

const PLAN_RESPONSE_PLANNED = {
  plan: {
    id: "plan-team",
    name: "team",
    display_name: "Team",
    seat_cap: 25,
    rpm_limit_default: 600,
    tpm_limit_default: 120000,
  },
  resolved: {
    effective_budget_usd_monthly: "500.00",
    plan_model_allowlist: null,
    plan_feature_flags: ["logs_explorer", "batch"],
  },
};

const BUDGET_120_OF_500 = { budget_usd_monthly: "500.00", spent_usd_month: "120.00" };
const SIX_MEMBERS = {
  users: Array.from({ length: 6 }, (_, i) => ({ id: `u${i}`, email: `u${i}@t.io`, role: "member" })),
};

const SELF_SERVE_PLANS_RESPONSE = {
  plans: [
    {
      id: "11111111-1111-1111-1111-111111111111",
      name: "starter",
      display_name: "Starter",
      base_price_usd_monthly: "1.00",
    },
    {
      id: "22222222-2222-2222-2222-222222222222",
      name: "pro",
      display_name: "Pro",
      base_price_usd_monthly: "20.00",
    },
  ],
};

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}
function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}
function renderPlan() {
  return render(<PlanSeatsPage />, { wrapper: Wrapper });
}
function section() {
  return screen.getByRole("region");
}

function mockCommonReads() {
  server.use(
    http.get(PLAN_URL, () => HttpResponse.json(PLAN_RESPONSE_PLANNED)),
    http.get(BUDGET_URL, () => HttpResponse.json(BUDGET_120_OF_500)),
    http.get(USERS_URL, () => HttpResponse.json(SIX_MEMBERS)),
  );
}

describe("PlanSeatsPage — live self-serve upgrade catalog (M4)", () => {
  it("test_upgrade_dialog_menu_lists_exactly_the_plans_admin_plans_returns", async () => {
    mockCommonReads();
    server.use(http.get(SELF_SERVE_PLANS_URL, () => HttpResponse.json(SELF_SERVE_PLANS_RESPONSE)));

    renderPlan();
    await waitFor(() => expect(within(section()).getByText("Team")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /upgrade plan/i }));

    const dialog = await screen.findByRole("dialog");
    await waitFor(() => {
      expect(within(dialog).getByRole("option", { name: "Starter" })).toBeInTheDocument();
    });
    expect(within(dialog).getByRole("option", { name: "Pro" })).toBeInTheDocument();
    // exactly those two — no stray hardcoded/leftover options besides the placeholder
    expect(within(dialog).getAllByRole("option")).toHaveLength(3);
  });

  it("test_empty_catalog_still_renders_dialog_with_honest_empty_state_not_a_crash", async () => {
    mockCommonReads();
    server.use(http.get(SELF_SERVE_PLANS_URL, () => HttpResponse.json({ plans: [] })));

    renderPlan();
    await waitFor(() => expect(within(section()).getByText("Team")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /upgrade plan/i }));

    const dialog = await screen.findByRole("dialog");
    await waitFor(() => {
      expect(within(dialog).getByText(/no upgrade options/i)).toBeInTheDocument();
    });
  });
});
