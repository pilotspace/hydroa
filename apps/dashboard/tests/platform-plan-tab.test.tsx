/**
 * tests/platform-plan-tab.test.tsx — RED suite for the plan-admin-ui task's
 * per-tenant "Plan" tab (Screen 2, 5th tab on the tenant-detail shell) — TASK.md
 * M3-M11.
 *
 * Fetches GET /admin/platform/tenants/{tenantId} (for `.kind`, the M8
 * pre-empt), GET/PUT /admin/platform/tenants/{tenantId}/plan, and GET
 * /admin/platform/plans — all byte-identical to platform_plans_router.py's own
 * frozen DTOs (plan-catalog §3 CONTRACT, confirmed by reading it directly this
 * session).
 *
 * RED before Build: `PlatformPlanTab` does not exist yet.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { PlatformPlanTab } from "@/components/platform/PlatformPlanTab";

const APP = "http://localhost:3000";
const TENANT_ID = "11111111-1111-1111-1111-111111111111";
const PLATFORM_TENANT_ID = "22222222-2222-2222-2222-222222222222";

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

function renderTab(tenantId: string = TENANT_ID) {
  const client = makeQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <PlatformPlanTab tenantId={tenantId} />
    </QueryClientProvider>,
  );
}

/** Wires the 3 GETs PlatformPlanTab depends on with sane defaults, overridable per test. */
function mockCommon(opts: {
  tenantId?: string;
  kind?: string;
  plan?: typeof STARTER | null;
  seat_cap?: number | null;
}) {
  const tenantId = opts.tenantId ?? TENANT_ID;
  const kind = opts.kind ?? "customer";
  const plan = opts.plan ?? null;
  const seat_cap = opts.seat_cap ?? null;
  server.use(
    http.get(`${APP}/api/gw/admin/platform/tenants/${tenantId}`, () =>
      HttpResponse.json({
        id: tenantId,
        name: "Acme Co",
        kind,
        created_at: "2026-01-01T00:00:00Z",
      }),
    ),
    http.get(`${APP}/api/gw/admin/platform/tenants/${tenantId}/plan`, () =>
      HttpResponse.json({ tenant_id: tenantId, plan, seat_cap }),
    ),
    http.get(`${APP}/api/gw/admin/platform/plans`, () => HttpResponse.json({ plans: ALL_PLANS })),
  );
}

describe("PlatformPlanTab — viewing", () => {
  it("test_unplanned_tenant_shows_no_plan_assigned_and_all_tiers_assignable", async () => {
    mockCommon({ plan: null, seat_cap: null });

    renderTab();

    await waitFor(() => {
      expect(screen.getByText(/no plan assigned/i)).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /^assign starter$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^assign team$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^assign enterprise$/i })).toBeInTheDocument();
    expect(screen.queryByText(/current plan/i)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /remove plan assignment/i }),
    ).not.toBeInTheDocument();
  });

  it("test_assigned_tenant_marks_current_tier_and_shows_others_for_comparison", async () => {
    mockCommon({ plan: TEAM, seat_cap: 25 });

    renderTab();

    await waitFor(() => {
      expect(screen.getByText(/current plan/i)).toBeInTheDocument();
    });
    // The Team card carries the "Current plan" marker (no Assign button of its
    // own); Starter/Enterprise render alongside it for comparison, each still
    // offering their own Assign action.
    expect(screen.getByRole("button", { name: /^assign starter$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^assign enterprise$/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^assign team$/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /adjust seat cap/i })).toBeInTheDocument();
    expect(screen.queryByText(/no plan assigned/i)).not.toBeInTheDocument();
  });
});

describe("PlatformPlanTab — assign / switch", () => {
  it("test_assign_plan_with_default_seat_cap_omits_seat_cap_in_request", async () => {
    mockCommon({ plan: null, seat_cap: null });
    let capturedBody: unknown = null;
    server.use(
      http.put(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({
          tenant_id: TENANT_ID,
          plan: STARTER,
          seat_cap: STARTER.seat_cap,
        });
      }),
    );

    renderTab();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^assign starter$/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /^assign starter$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });
    expect(capturedBody).toEqual({ plan_id: STARTER.id });
    expect(capturedBody).not.toHaveProperty("seat_cap");
  });

  it("test_assign_plan_with_edited_seat_cap_sends_explicit_override", async () => {
    mockCommon({ plan: null, seat_cap: null });
    let capturedBody: unknown = null;
    server.use(
      http.put(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({ tenant_id: TENANT_ID, plan: ENTERPRISE, seat_cap: 47 });
      }),
    );

    renderTab();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^assign enterprise$/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /^assign enterprise$/i }));
    const input = screen.getByLabelText(/^seats$/i);
    fireEvent.change(input, { target: { value: "47" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(capturedBody).toEqual({ plan_id: ENTERPRISE.id, seat_cap: 47 });
    });
    await waitFor(() => {
      expect(screen.getByText("47")).toBeInTheDocument();
    });
  });

  it("test_switch_tier_updates_which_card_is_marked_current", async () => {
    mockCommon({ plan: STARTER, seat_cap: 3 });
    server.use(
      http.put(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, () =>
        HttpResponse.json({ tenant_id: TENANT_ID, plan: TEAM, seat_cap: null }),
      ),
    );

    renderTab();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^assign team$/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /^assign team$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /^assign team$/i })).not.toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /^assign starter$/i })).toBeInTheDocument();
  });
});

describe("PlatformPlanTab — adjust seat cap only", () => {
  it("test_adjust_seat_cap_without_changing_tier", async () => {
    mockCommon({ plan: ENTERPRISE, seat_cap: null });
    let capturedBody: unknown = null;
    server.use(
      http.put(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({ tenant_id: TENANT_ID, plan: ENTERPRISE, seat_cap: 120 });
      }),
    );

    renderTab();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /adjust seat cap/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /adjust seat cap/i }));
    const input = screen.getByLabelText(/^seats$/i);
    fireEvent.change(input, { target: { value: "120" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(capturedBody).toEqual({ plan_id: ENTERPRISE.id, seat_cap: 120 });
    });
    await waitFor(() => {
      expect(screen.getByText("120")).toBeInTheDocument();
    });
    // Tier is unchanged — still Enterprise, still marked current.
    expect(screen.getByText(/current plan/i)).toBeInTheDocument();
  });

  it("test_adjust_seat_cap_save_without_touching_input_preserves_custom_cap_explicitly", async () => {
    // Regression test [review finding]: a tenant with a CUSTOM (non-default) seat_cap
    // on their CURRENT plan opens "Adjust seat cap" (pre-filled with that custom
    // value, see openConfirm) and clicks Save WITHOUT editing the input. Before the
    // fix, the untouched-flag branch unconditionally OMITTED seat_cap from the PUT
    // body — which the backend reads as "inherit the plan's own default" — silently
    // discarding the tenant's negotiated custom cap. The fix must send seat_cap
    // EXPLICITLY (the known current value) whenever an untouched Save targets the
    // tenant's CURRENT plan, distinct from the assign/switch-tier untouched case
    // (previous test in this file) which correctly still omits it.
    mockCommon({ plan: ENTERPRISE, seat_cap: 200 });
    let capturedBody: unknown = null;
    server.use(
      http.put(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({ tenant_id: TENANT_ID, plan: ENTERPRISE, seat_cap: 200 });
      }),
    );

    renderTab();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /adjust seat cap/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /adjust seat cap/i }));
    // Deliberately do NOT touch the seat-cap input — Save immediately.
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });
    expect(capturedBody).toEqual({ plan_id: ENTERPRISE.id, seat_cap: 200 });
  });
});

describe("PlatformPlanTab — remove plan assignment", () => {
  it("test_remove_plan_assignment_cancel_makes_no_request", async () => {
    mockCommon({ plan: TEAM, seat_cap: 25 });
    let putCalled = false;
    server.use(
      http.put(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, () => {
        putCalled = true;
        return HttpResponse.json({ tenant_id: TENANT_ID, plan: null, seat_cap: null });
      }),
    );

    renderTab();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /remove plan assignment/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /remove plan assignment/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /^cancel$/i }));

    expect(putCalled).toBe(false);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText(/current plan/i)).toBeInTheDocument();
  });

  it("test_remove_plan_assignment_confirm_clears_atomically", async () => {
    mockCommon({ plan: TEAM, seat_cap: 25 });
    let capturedBody: unknown = null;
    server.use(
      http.put(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({ tenant_id: TENANT_ID, plan: null, seat_cap: null });
      }),
    );

    renderTab();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /remove plan assignment/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /remove plan assignment/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /^confirm$/i }));

    await waitFor(() => {
      expect(capturedBody).toEqual({ plan_id: null });
    });
    expect(capturedBody).not.toHaveProperty("seat_cap");
    await waitFor(() => {
      expect(screen.getByText(/no plan assigned/i)).toBeInTheDocument();
    });
  });
});

describe("PlatformPlanTab — platform tenant is read-only", () => {
  it("test_platform_tenant_shows_ineligible_note_not_a_form", async () => {
    mockCommon({ tenantId: PLATFORM_TENANT_ID, kind: "platform", plan: null, seat_cap: null });

    renderTab(PLATFORM_TENANT_ID);

    await waitFor(() => {
      expect(screen.getByText(/not eligible for plan assignment/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: /^assign/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /adjust seat cap/i })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /remove plan assignment/i }),
    ).not.toBeInTheDocument();
  });
});

describe("PlatformPlanTab — client-side validation", () => {
  it("test_seat_cap_client_side_validation_blocks_invalid_values", async () => {
    mockCommon({ plan: null, seat_cap: null });
    let putCalled = false;
    server.use(
      http.put(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, () => {
        putCalled = true;
        return HttpResponse.json({ tenant_id: TENANT_ID, plan: STARTER, seat_cap: 1 });
      }),
    );

    renderTab();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^assign starter$/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /^assign starter$/i }));
    const input = screen.getByLabelText(/^seats$/i);

    for (const bad of ["0", "-5", "abc"]) {
      fireEvent.change(input, { target: { value: bad } });
      fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
      await waitFor(() => {
        expect(screen.getByRole("alert")).toBeInTheDocument();
      });
    }
    expect(putCalled).toBe(false);
  });
});

describe("PlatformPlanTab — named server errors surfaced as clear alerts", () => {
  it("test_server_rejects_with_plan_tenant_ineligible_shown_as_alert", async () => {
    mockCommon({ plan: null, seat_cap: null });
    server.use(
      http.put(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, () =>
        HttpResponse.json(
          {
            code: "ERR_PLAN_TENANT_INELIGIBLE",
            title: "This tenant is not eligible for plan assignment",
          },
          { status: 403 },
        ),
      ),
    );

    renderTab();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^assign starter$/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /^assign starter$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/not eligible for plan assignment/i);
    });
  });

  it("test_server_rejects_with_plan_not_found_shown_as_alert", async () => {
    mockCommon({ plan: null, seat_cap: null });
    server.use(
      http.put(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, () =>
        HttpResponse.json({ code: "ERR_PLAN_NOT_FOUND", title: "Plan not found" }, { status: 404 }),
      ),
    );

    renderTab();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^assign starter$/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /^assign starter$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/plan not found/i);
    });
    // The failed attempt is not optimistically applied.
    expect(screen.getByText(/no plan assigned/i)).toBeInTheDocument();
  });
});

describe("PlatformPlanTab — fetch failure", () => {
  it("test_plan_fetch_failure_shows_error_state_with_retry", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}`, () =>
        HttpResponse.json({
          id: TENANT_ID,
          name: "Acme Co",
          kind: "customer",
          created_at: "2026-01-01T00:00:00Z",
        }),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, () =>
        HttpResponse.json({ title: "Internal server error", status: 500 }, { status: 500 }),
      ),
      http.get(`${APP}/api/gw/admin/platform/plans`, () => HttpResponse.json({ plans: ALL_PLANS })),
    );

    renderTab();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    });
    expect(screen.queryByText(/no plan assigned/i)).not.toBeInTheDocument();
  });
});

describe("PlatformPlanTab — double-submit guard", () => {
  it("test_assign_button_disabled_while_mutation_pending", async () => {
    mockCommon({ plan: null, seat_cap: null });
    let putCallCount = 0;
    server.use(
      http.put(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, async () => {
        putCallCount += 1;
        await new Promise((resolve) => setTimeout(resolve, 50));
        return HttpResponse.json({
          tenant_id: TENANT_ID,
          plan: STARTER,
          seat_cap: STARTER.seat_cap,
        });
      }),
    );

    renderTab();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^assign starter$/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /^assign starter$/i }));

    const saveButton = screen.getByRole("button", { name: /^save$/i });
    fireEvent.click(saveButton);
    await waitFor(() => {
      expect(saveButton).toBeDisabled();
    });
    // A second click while disabled must not fire a second request.
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(screen.getByText(/current plan/i)).toBeInTheDocument();
    });
    expect(putCallCount).toBe(1);
  });
});

describe("PlatformPlanTab — console-flat-visual-pass", () => {
  // M1 — the shared PlanCard goes flat at this Screen-2 call site too (same
  // component as Screen 1's catalog page — see Issues/Risks #4).
  it("test_plan_cards_render_flat_variant", async () => {
    mockCommon({ plan: TEAM, seat_cap: 25 });
    renderTab();
    await waitFor(() => {
      expect(screen.getByText(/current plan/i)).toBeInTheDocument();
    });
    const starterHeading = screen.getByRole("heading", { name: "Starter" });
    const starterCard = starterHeading.closest("[data-variant]")!;
    expect(starterCard).toHaveAttribute("data-variant", "flat");
    expect(starterCard.className).toContain("rounded-[var(--radius-flat-card)]");
    expect(starterCard.className).not.toContain("border-border");
  });

  // M4 — the current-assigned PlanCard renders BOTH the selected-border
  // (border-primary) AND the "Current plan" badge, together; a non-assigned
  // card renders neither.
  it("test_current_plan_card_shows_selected_border_and_badge_together", async () => {
    mockCommon({ plan: TEAM, seat_cap: 25 });
    renderTab();
    await waitFor(() => {
      expect(screen.getByText(/current plan/i)).toBeInTheDocument();
    });

    const teamHeading = screen.getByRole("heading", { name: "Team" });
    const teamCard = teamHeading.closest("[data-variant]")! as HTMLElement;
    expect(teamCard.className).toContain("border-primary");
    expect(within(teamCard).getByText(/current plan/i)).toBeInTheDocument();

    const starterHeading = screen.getByRole("heading", { name: "Starter" });
    const starterCard = starterHeading.closest("[data-variant]")! as HTMLElement;
    expect(starterCard.className).not.toContain("border-primary");
    expect(within(starterCard).queryByText(/current plan/i)).not.toBeInTheDocument();
  });

  // M5 — the "Current plan" Badge, the Adjust/Assign/Save/Cancel/Remove Buttons,
  // and the seat-cap Input all get the page-local flat-control/flat-tag radius.
  it("test_plan_tab_badge_button_input_get_flat_radius_class", async () => {
    mockCommon({ plan: TEAM, seat_cap: 25 });
    renderTab();
    await waitFor(() => {
      expect(screen.getByText(/current plan/i)).toBeInTheDocument();
    });

    expect(screen.getByText(/current plan/i).className).toContain(
      "rounded-[var(--radius-flat-tag)]",
    );
    expect(screen.getByRole("button", { name: /adjust seat cap/i }).className).toContain(
      "rounded-[var(--radius-flat-control)]",
    );
    expect(screen.getByRole("button", { name: /^assign starter$/i }).className).toContain(
      "rounded-[var(--radius-flat-control)]",
    );
    expect(screen.getByRole("button", { name: /remove plan assignment/i }).className).toContain(
      "rounded-[var(--radius-flat-control)]",
    );

    fireEvent.click(screen.getByRole("button", { name: /adjust seat cap/i }));
    const input = screen.getByLabelText(/^seats$/i);
    expect(input.className).toContain("rounded-[var(--radius-flat-control)]");
    expect(screen.getByRole("button", { name: /^save$/i }).className).toContain(
      "rounded-[var(--radius-flat-control)]",
    );
    expect(screen.getByRole("button", { name: /^cancel$/i }).className).toContain(
      "rounded-[var(--radius-flat-control)]",
    );
  });

  // M6 (verify-only) — the remove-plan-assignment confirm dialog keeps its
  // existing elevated treatment unchanged.
  it("test_remove_plan_confirm_dialog_stays_elevated_unchanged", async () => {
    mockCommon({ plan: TEAM, seat_cap: 25 });
    renderTab();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /remove plan assignment/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /remove plan assignment/i }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.className).toContain("rounded-lg");
    expect(dialog.className).toContain("border-border");
    expect(dialog.className).toContain("shadow-lg");
    expect(dialog.className).not.toContain("rounded-[var(--radius-flat-card)]");
    const confirmBtn = within(dialog).getByRole("button", { name: /confirm/i });
    expect(confirmBtn.className).not.toContain("rounded-[var(--radius-flat-control)]");
  });
});
