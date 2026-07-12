/**
 * tests/billing-plan.test.tsx — RED suite for the Plan & seats page
 * (billing-ui TASK.md §3 — FROZEN @ v1, M7, M8 UI consumption, R6, R7, M11).
 *
 * RED before Build: `@/components/plan/PlanSeatsPage` does not exist yet ->
 * MODULE_NOT_FOUND, the established true-red convention.
 */

import { describe, expect, it } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { axe } from "@/test-support/axe";
import React from "react";

import { PlanSeatsPage } from "@/components/plan/PlanSeatsPage";

const APP = "http://localhost:3000";
const PLAN_URL = `${APP}/api/gw/admin/plan`;
const BUDGET_URL = `${APP}/api/gw/admin/budget`;
const USERS_URL = `${APP}/api/gw/admin/users`;

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

const PLAN_RESPONSE_UNPLANNED = {
  plan: null,
  resolved: { effective_budget_usd_monthly: null, plan_model_allowlist: null, plan_feature_flags: [] },
};

const PLAN_RESPONSE_UNPLANNED_WITH_BUDGET = {
  plan: null,
  resolved: { effective_budget_usd_monthly: "30.00", plan_model_allowlist: null, plan_feature_flags: [] },
};

const BUDGET_120_OF_500 = { budget_usd_monthly: "500.00", spent_usd_month: "120.00" };
const BUDGET_30 = { budget_usd_monthly: "30.00", spent_usd_month: "30.00" };

const SIX_MEMBERS = {
  users: Array.from({ length: 6 }, (_, i) => ({ id: `u${i}`, email: `u${i}@t.io`, role: "member" })),
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

describe("PlanSeatsPage — planned tenant (M7, M8)", () => {
  it("test_planned_tenant_sees_plan_budget_allowlist_features_and_seat_count", async () => {
    server.use(
      http.get(PLAN_URL, () => HttpResponse.json(PLAN_RESPONSE_PLANNED)),
      http.get(BUDGET_URL, () => HttpResponse.json(BUDGET_120_OF_500)),
      http.get(USERS_URL, () => HttpResponse.json(SIX_MEMBERS)),
    );
    renderPlan();

    await waitFor(() => expect(within(section()).getByText("Team")).toBeInTheDocument());
    expect(within(section()).getByText(/\$120\.00/)).toBeInTheDocument();
    expect(within(section()).getByText(/\$500\.00/)).toBeInTheDocument();
    expect(within(section()).getByText(/all models/i)).toBeInTheDocument();
    expect(within(section()).getByText("logs_explorer")).toBeInTheDocument();
    expect(within(section()).getByText("batch")).toBeInTheDocument();
    expect(within(section()).getByText(/6 of 25 seats/i)).toBeInTheDocument();
  });

  it("test_seat_billing_unfrozen_pricing_slot_degrades_gracefully", async () => {
    server.use(
      http.get(PLAN_URL, () => HttpResponse.json(PLAN_RESPONSE_PLANNED)),
      http.get(BUDGET_URL, () => HttpResponse.json(BUDGET_120_OF_500)),
      http.get(USERS_URL, () => HttpResponse.json(SIX_MEMBERS)),
    );
    renderPlan();

    await waitFor(() => expect(within(section()).getByText("Team")).toBeInTheDocument());
    expect(within(section()).getByText(/seat pricing coming soon/i)).toBeInTheDocument();
    // the rest of the page (plan, meter, allowlist, features, seat count) is unaffected
    expect(within(section()).getByText(/6 of 25 seats/i)).toBeInTheDocument();
  });
});

describe("PlanSeatsPage — unplanned tenant (R6)", () => {
  it("test_unplanned_tenant_sees_honest_no_plan_state_not_an_error", async () => {
    server.use(
      http.get(PLAN_URL, () => HttpResponse.json(PLAN_RESPONSE_UNPLANNED_WITH_BUDGET)),
      http.get(BUDGET_URL, () => HttpResponse.json(BUDGET_30)),
      http.get(USERS_URL, () => HttpResponse.json(SIX_MEMBERS)),
    );
    renderPlan();

    await waitFor(() =>
      expect(within(section()).getByText(/no plan assigned/i)).toBeInTheDocument(),
    );
    expect(within(section()).getByText(/usage governed by tenant-level defaults only/i)).toBeInTheDocument();
    // the tenant-level resolved budget still renders — the data remains meaningful
    expect(within(section()).getByText(/\$30\.00/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("test_budget_meter_renders_unlimited_text_only_when_ceiling_is_null_never_a_percentage", async () => {
    server.use(
      http.get(PLAN_URL, () => HttpResponse.json(PLAN_RESPONSE_UNPLANNED)),
      http.get(BUDGET_URL, () => HttpResponse.json({ budget_usd_monthly: null, spent_usd_month: "0.00" })),
      http.get(USERS_URL, () => HttpResponse.json(SIX_MEMBERS)),
    );
    renderPlan();

    await waitFor(() => expect(within(section()).getByText(/no plan assigned/i)).toBeInTheDocument());
    expect(within(section()).getByText(/unlimited/i)).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
});

describe("PlanSeatsPage — a11y (M11)", () => {
  it("test_axe_no_serious_violations", async () => {
    server.use(
      http.get(PLAN_URL, () => HttpResponse.json(PLAN_RESPONSE_PLANNED)),
      http.get(BUDGET_URL, () => HttpResponse.json(BUDGET_120_OF_500)),
      http.get(USERS_URL, () => HttpResponse.json(SIX_MEMBERS)),
    );
    const { container } = renderPlan();
    await waitFor(() => expect(within(section()).getByText("Team")).toBeInTheDocument());
    const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toHaveLength(0);
  });
});
