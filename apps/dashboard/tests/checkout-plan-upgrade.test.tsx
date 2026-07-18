/**
 * tests/checkout-plan-upgrade.test.tsx — RED-first suite for UpgradePlanDialog
 * (self-serve-checkout TASK.md §3 — FROZEN @ v1, M8/M9).
 *
 * RED before Build: `@/components/checkout/UpgradePlanDialog` does not exist yet ->
 * MODULE_NOT_FOUND. Exercises the plan-select → server-preview (current/target base + delta)
 * → confirm flow, the empty-options honest degrade, the plan_unchanged `{error}` mapping, and
 * an axe pass.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { axe } from "@/test-support/axe";

import { UpgradePlanDialog, type UpgradePlanOption } from "@/components/checkout/UpgradePlanDialog";

const APP = "http://localhost:3000";
const CREATE_URL = `${APP}/api/gw/admin/checkout/plan-upgrade`;
const confirmUrl = (id: string) => `${APP}/api/gw/admin/checkout/${id}/confirm`;

const PLANS: UpgradePlanOption[] = [
  { id: "11111111-1111-1111-1111-111111111111", displayName: "Pro" },
  { id: "22222222-2222-2222-2222-222222222222", displayName: "Team" },
];

const PREVIEW = {
  session_id: "sess-upg-1",
  provider: "dev",
  status: "pending",
  redirect_url: null,
  preview: {
    intent: "plan_upgrade",
    current_plan: "starter",
    target_plan: "pro",
    current_base_usd: "1.00",
    target_base_usd: "20.00",
    delta_usd: "19.00",
    currency: "USD",
    effective: "immediate",
  },
};

function renderDialog(plans: UpgradePlanOption[] = PLANS) {
  const onClose = vi.fn();
  const onSuccess = vi.fn();
  render(<UpgradePlanDialog isOpen onClose={onClose} onSuccess={onSuccess} availablePlans={plans} />);
  return { onClose, onSuccess };
}

function dialog() {
  return screen.getByRole("dialog");
}

describe("UpgradePlanDialog — upgrade with server price math (M8/M9)", () => {
  it("test_select_review_shows_price_math_then_confirm_succeeds", async () => {
    let confirmCalled = false;
    server.use(
      http.post(CREATE_URL, () => HttpResponse.json(PREVIEW)),
      http.post(confirmUrl("sess-upg-1"), () => {
        confirmCalled = true;
        return HttpResponse.json({
          session_id: "sess-upg-1",
          provider: "dev",
          status: "succeeded",
          applied: { plan_id: "11111111-1111-1111-1111-111111111111" },
        });
      }),
    );
    const { onSuccess } = renderDialog();

    fireEvent.change(screen.getByLabelText(/target plan/i), {
      target: { value: "11111111-1111-1111-1111-111111111111" },
    });
    fireEvent.click(screen.getByRole("button", { name: /review upgrade/i }));

    await waitFor(() => expect(screen.getByTestId("upgrade-delta")).toBeInTheDocument());
    expect(screen.getByTestId("upgrade-current-plan")).toHaveTextContent(/starter/i);
    expect(screen.getByTestId("upgrade-current-plan")).toHaveTextContent("$1.00");
    expect(screen.getByTestId("upgrade-target-plan")).toHaveTextContent(/pro/i);
    expect(screen.getByTestId("upgrade-target-plan")).toHaveTextContent("$20.00");
    expect(screen.getByTestId("upgrade-delta")).toHaveTextContent("$19.00");
    expect(within(dialog()).getByText(/takes effect immediate/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /confirm upgrade/i }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
    expect(confirmCalled).toBe(true);
  });

  it("test_no_plan_selected_shows_validation_without_calling_server", async () => {
    let created = false;
    server.use(
      http.post(CREATE_URL, () => {
        created = true;
        return HttpResponse.json(PREVIEW);
      }),
    );
    renderDialog();

    fireEvent.click(screen.getByRole("button", { name: /review upgrade/i }));

    expect(await within(dialog()).findByRole("alert")).toHaveTextContent(/choose a plan/i);
    expect(created).toBe(false);
  });

  it("test_plan_unchanged_maps_to_friendly_error", async () => {
    server.use(
      http.post(CREATE_URL, () => HttpResponse.json({ error: "plan_unchanged" }, { status: 422 })),
    );
    renderDialog();

    fireEvent.change(screen.getByLabelText(/target plan/i), {
      target: { value: "11111111-1111-1111-1111-111111111111" },
    });
    fireEvent.click(screen.getByRole("button", { name: /review upgrade/i }));

    expect(await within(dialog()).findByRole("alert")).toHaveTextContent(/already on that plan/i);
  });

  it("test_empty_options_degrades_to_honest_no_options_state", () => {
    renderDialog([]);
    expect(within(dialog()).getByText(/no upgrade options/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /review upgrade/i })).not.toBeInTheDocument();
  });

  it("test_axe_no_serious_violations", async () => {
    const { container } = render(
      <UpgradePlanDialog isOpen onClose={() => {}} onSuccess={() => {}} availablePlans={PLANS} />,
    );
    const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toHaveLength(0);
  });
});
