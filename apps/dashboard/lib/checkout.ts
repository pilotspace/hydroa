/**
 * lib/checkout.ts — client adapter for the tenant self-serve checkout seam
 * (self-serve-checkout TASK.md §3 CONTRACT — FROZEN @ v1).
 *
 * Two-phase, idempotent flow the dialogs drive:
 *   1. create  — POST /admin/checkout/plan-upgrade | credit-topup  (server computes the
 *      `preview` price math; idempotency_key travels in the BODY, never a header — the BFF
 *      strips custom headers, TASK.md I4/A6).
 *   2. confirm — POST /admin/checkout/{session_id}/confirm  (row-locked, applies the mutation
 *      exactly once; a re-confirm replays the same result).
 *
 * Errors: the checkout endpoints use the `{ "error": "<token>" }` envelope (NOT the RFC 9457
 * ProblemDetail the rest of the control plane uses). `BffError.problem` therefore carries an
 * `error` field, not `title` — `checkoutErrorMessage` maps the token to human copy.
 */

import { bffPost, BffError } from "@/lib/bff-client";

export type PlanUpgradePreview = {
  intent: "plan_upgrade";
  current_plan: string | null;
  target_plan: string;
  current_base_usd: string;
  target_base_usd: string;
  delta_usd: string;
  currency: string;
  effective: string;
};

export type CreditTopupPreview = {
  intent: "credit_topup";
  amount_usd: string;
  currency: string;
  balance_before_usd: string;
  balance_after_preview_usd: string;
};

export type CheckoutCreateResponse<P> = {
  session_id: string;
  provider: string;
  status: string;
  redirect_url?: string | null;
  preview: P;
};

export type CheckoutConfirmResponse = {
  session_id: string;
  provider: string;
  status: string;
  applied: Record<string, unknown>;
};

/** A fresh idempotency key per create attempt (a retried create with the SAME key returns the
 * SAME session — TASK.md M4). Uses the platform crypto UUID (available in the browser and in
 * the jsdom/node test runtime). */
export function newIdempotencyKey(): string {
  return globalThis.crypto.randomUUID();
}

export async function createPlanUpgrade(
  targetPlanId: string,
  idempotencyKey: string,
): Promise<CheckoutCreateResponse<PlanUpgradePreview>> {
  return bffPost<CheckoutCreateResponse<PlanUpgradePreview>>("/admin/checkout/plan-upgrade", {
    target_plan_id: targetPlanId,
    idempotency_key: idempotencyKey,
  });
}

export async function createCreditTopup(
  amountUsd: string,
  idempotencyKey: string,
  note?: string,
): Promise<CheckoutCreateResponse<CreditTopupPreview>> {
  return bffPost<CheckoutCreateResponse<CreditTopupPreview>>("/admin/checkout/credit-topup", {
    amount_usd: amountUsd,
    idempotency_key: idempotencyKey,
    ...(note ? { note } : {}),
  });
}

export async function confirmCheckout(sessionId: string): Promise<CheckoutConfirmResponse> {
  return bffPost<CheckoutConfirmResponse>(`/admin/checkout/${sessionId}/confirm`, {});
}

const CHECKOUT_ERROR_COPY: Record<string, string> = {
  idempotency_key_required: "Something went wrong preparing this request. Please try again.",
  idempotency_key_conflict: "A different request is already in progress. Please try again.",
  checkout_disabled: "Self-serve checkout is currently turned off. Contact your platform operator.",
  checkout_not_found: "This checkout could not be found.",
  checkout_not_confirmable: "This checkout can no longer be confirmed. Start a new one.",
  plan_not_found: "That plan is not available.",
  plan_not_self_serve: "That plan can't be self-served — contact sales.",
  plan_account_type_mismatch: "That plan isn't available for this account type.",
  plan_downgrade_not_self_serve: "Downgrades can't be self-served — contact your platform operator.",
  plan_unchanged: "You're already on that plan.",
  amount_invalid: "Enter a valid amount greater than $0.",
  amount_exceeds_max: "That amount is above the self-serve top-up limit.",
  payment_provider_unavailable: "The payment provider is temporarily unavailable. Please try again shortly.",
  ERR_AUTH_FORBIDDEN: "You don't have permission to do this.",
};

/** The checkout `{error}` token if present, else null. */
export function checkoutErrorToken(err: unknown): string | null {
  if (err instanceof BffError) {
    const token = (err.problem as { error?: unknown }).error;
    if (typeof token === "string") return token;
  }
  return null;
}

/** Human copy for a checkout failure — maps the `{error}` token, falling back to the
 * ProblemDetail title, then a generic message. */
export function checkoutErrorMessage(err: unknown, fallback = "Something went wrong. Please try again."): string {
  const token = checkoutErrorToken(err);
  if (token && token in CHECKOUT_ERROR_COPY) return CHECKOUT_ERROR_COPY[token]!;
  if (token) return token;
  if (err instanceof BffError && typeof err.problem.title === "string") return err.problem.title;
  return fallback;
}
