/**
 * tests/checkout-credit-topup.test.tsx — RED-first suite for AddCreditsDialog
 * (self-serve-checkout TASK.md §3 — FROZEN @ v1, M6/M9).
 *
 * RED before Build: `@/components/checkout/AddCreditsDialog` does not exist yet ->
 * MODULE_NOT_FOUND, the established true-red convention. Exercises the full
 * input → server-preview (price math) → confirm flow plus the `{error}`-token mapping
 * for amount_invalid / amount_exceeds_max, and an axe pass.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { axe } from "@/test-support/axe";

import { AddCreditsDialog } from "@/components/checkout/AddCreditsDialog";

const APP = "http://localhost:3000";
const CREATE_URL = `${APP}/api/gw/admin/checkout/credit-topup`;
const confirmUrl = (id: string) => `${APP}/api/gw/admin/checkout/${id}/confirm`;

const PREVIEW = {
  session_id: "sess-topup-1",
  provider: "dev",
  status: "pending",
  redirect_url: null,
  preview: {
    intent: "credit_topup",
    amount_usd: "50.00",
    currency: "USD",
    balance_before_usd: "42.50",
    balance_after_preview_usd: "92.50",
  },
};

function renderDialog(overrides: Partial<React.ComponentProps<typeof AddCreditsDialog>> = {}) {
  const onClose = vi.fn();
  const onSuccess = vi.fn();
  render(<AddCreditsDialog isOpen onClose={onClose} onSuccess={onSuccess} {...overrides} />);
  return { onClose, onSuccess };
}

function dialog() {
  return screen.getByRole("dialog");
}

describe("AddCreditsDialog — top-up with server price math (M6/M9)", () => {
  it("test_review_shows_server_price_math_then_confirm_succeeds", async () => {
    let confirmCalled = false;
    server.use(
      http.post(CREATE_URL, () => HttpResponse.json(PREVIEW)),
      http.post(confirmUrl("sess-topup-1"), () => {
        confirmCalled = true;
        return HttpResponse.json({
          session_id: "sess-topup-1",
          provider: "dev",
          status: "succeeded",
          applied: { entry_type: "topup", amount_usd: "50.00", balance_after_usd: "92.50" },
        });
      }),
    );
    const { onSuccess } = renderDialog();

    fireEvent.change(screen.getByLabelText(/amount/i), { target: { value: "50.00" } });
    fireEvent.click(screen.getByRole("button", { name: /review top-up/i }));

    await waitFor(() => expect(screen.getByTestId("topup-balance-after")).toBeInTheDocument());
    expect(screen.getByTestId("topup-amount")).toHaveTextContent("$50.00");
    expect(screen.getByTestId("topup-balance-after")).toHaveTextContent("$92.50");
    expect(within(dialog()).getByText("$42.50")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /confirm top-up/i }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
    expect(confirmCalled).toBe(true);
  });

  it("test_client_side_rejects_non_positive_amount_without_calling_server", async () => {
    let created = false;
    server.use(
      http.post(CREATE_URL, () => {
        created = true;
        return HttpResponse.json(PREVIEW);
      }),
    );
    renderDialog();

    fireEvent.change(screen.getByLabelText(/amount/i), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: /review top-up/i }));

    expect(await within(dialog()).findByRole("alert")).toHaveTextContent(/greater than \$0/i);
    expect(created).toBe(false);
  });

  it("test_amount_invalid_maps_to_friendly_error", async () => {
    server.use(
      http.post(CREATE_URL, () =>
        HttpResponse.json({ error: "amount_invalid" }, { status: 422 }),
      ),
    );
    renderDialog();

    fireEvent.change(screen.getByLabelText(/amount/i), { target: { value: "5.00" } });
    fireEvent.click(screen.getByRole("button", { name: /review top-up/i }));

    expect(await within(dialog()).findByRole("alert")).toHaveTextContent(/valid amount/i);
  });

  it("test_amount_exceeds_max_maps_to_friendly_error", async () => {
    server.use(
      http.post(CREATE_URL, () =>
        HttpResponse.json({ error: "amount_exceeds_max" }, { status: 422 }),
      ),
    );
    renderDialog();

    fireEvent.change(screen.getByLabelText(/amount/i), { target: { value: "999999.00" } });
    fireEvent.click(screen.getByRole("button", { name: /review top-up/i }));

    expect(await within(dialog()).findByRole("alert")).toHaveTextContent(/top-up limit/i);
  });

  it("test_axe_no_serious_violations", async () => {
    server.use(http.post(CREATE_URL, () => HttpResponse.json(PREVIEW)));
    const { container } = render(
      <AddCreditsDialog isOpen onClose={() => {}} onSuccess={() => {}} />,
    );
    const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toHaveLength(0);
  });
});
