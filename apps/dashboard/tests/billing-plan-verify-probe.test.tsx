/**
 * tests/billing-plan-verify-probe.test.tsx — independent VERIFY probe (billing-ui
 * TASK.md §6, add-verify pass), separate from the builder's own `billing-plan.test.tsx`.
 *
 * Reproduces finding F1 as an executable test rather than code-read inference alone:
 * `GET /admin/users` (the real, drifted ground anchor behind the Plan & seats page's
 * "live roster count" — §0 Known-problem fixes) requires `Permission.MEMBERS_MANAGE`
 * (owner/admin/superadmin only, confirmed in `gateway/tenants/domain/authz.py`). Yet
 * `/app/plan` itself carries NO `minRole` (M1) — every other role (operator,
 * billing_admin, viewer, member) can reach this page and WILL get a 403 on the users
 * read. `PlanSeatsPage.tsx`'s own comment claims this "degrades gracefully" — this
 * probe confirms that claim is true (no crash, no page-level ErrorState swallowing the
 * rest of the page) AND documents that the seat count is silently NOT live for these
 * roles, contra M7's "LIVE roster count" promise. Neither outcome was previously
 * covered by any test in `billing-plan.test.tsx`.
 */

import { describe, expect, it } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
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
    plan_feature_flags: [],
  },
};
const BUDGET_120_OF_500 = { budget_usd_monthly: "500.00", spent_usd_month: "120.00" };

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}
function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}
function section() {
  return screen.getByRole("region");
}

describe("PlanSeatsPage — VERIFY probe: seat-count degrade for MEMBERS_MANAGE-lacking roles (F1)", () => {
  it("test_operator_role_sees_dash_not_a_crash_when_admin_users_403s", async () => {
    // Mirrors what an operator/billing_admin/viewer/member session actually receives:
    // GET /admin/plan and /admin/budget succeed (any-role, M8/M7); GET /admin/users
    // 403s because MEMBERS_MANAGE is owner/admin/superadmin-only (authz.py:87-136).
    server.use(
      http.get(PLAN_URL, () => HttpResponse.json(PLAN_RESPONSE_PLANNED)),
      http.get(BUDGET_URL, () => HttpResponse.json(BUDGET_120_OF_500)),
      http.get(USERS_URL, () =>
        HttpResponse.json({ title: "Insufficient role", status: 403, code: "ERR_AUTH_FORBIDDEN" }, { status: 403 }),
      ),
    );
    render(<PlanSeatsPage />, { wrapper: Wrapper });

    // Claim 1 (the honest part): the page does NOT show a page-level ErrorState — the
    // users-read failure never blocks the rest of the page (isLoading/isError only gate
    // on planQuery/budgetQuery, confirmed by reading PlanSeatsPage.tsx:78-79).
    await waitFor(() => expect(within(section()).getByText("Team")).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    // Claim 2 (the gap): the seat count is NOT live for this role — it silently falls
    // back to "—" rather than the real "6 of 25" the design promises (DESIGN.md §6,
    // TASK.md M7 "a LIVE roster count"). This is the exact, previously-untested
    // behavior 4 of the 7 roles that can reach /app/plan (operator, billing_admin,
    // viewer, member) will see in production.
    expect(within(section()).getByText(/— of 25 seats/)).toBeInTheDocument();
    expect(within(section()).queryByText(/\d+ of 25 seats/)).not.toBeInTheDocument();
  });
});
