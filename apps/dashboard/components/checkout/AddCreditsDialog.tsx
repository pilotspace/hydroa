"use client";

/**
 * AddCreditsDialog — self-serve credit top-up (self-serve-checkout TASK.md §3, M6/M9).
 *
 * Mirrors CreateKeyDialog: rendered inline (not a native <dialog>) so jsdom can drive it,
 * zod `.safeParse` for the amount, `BffError`/checkout-token error mapping, focus trap.
 *
 * Two explicit phases so the user sees SERVER-computed price math before any mutation:
 *   input   → POST /admin/checkout/credit-topup  → shows { amount, balance before/after }
 *   preview → POST /admin/checkout/{id}/confirm   → applies the top-up, then onSuccess().
 * The idempotency key is minted once per preview; a network retry of the same create is safe.
 */

import { useState, FormEvent } from "react";
import { z } from "zod";
import { Input, Button } from "@/components/ui";
import { useFocusTrap } from "@/lib/use-focus-trap";
import { formatUsd } from "@/lib/format";
import {
  createCreditTopup,
  confirmCheckout,
  checkoutErrorMessage,
  newIdempotencyKey,
  type CreditTopupPreview,
} from "@/lib/checkout";

const AmountSchema = z
  .string()
  .trim()
  .min(1, "Enter an amount")
  .refine((v) => {
    const n = Number(v);
    return Number.isFinite(n) && n > 0;
  }, "Enter a valid amount greater than $0");

interface AddCreditsDialogProps {
  isOpen: boolean;
  onClose: () => void;
  /** Called after a successful confirm — the caller refetches balance + history. */
  onSuccess: () => void;
}

export function AddCreditsDialog({ isOpen, onClose, onSuccess }: AddCreditsDialogProps) {
  const [amount, setAmount] = useState("");
  const [amountError, setAmountError] = useState<string | null>(null);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [preview, setPreview] = useState<CreditTopupPreview | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function reset() {
    setAmount("");
    setAmountError(null);
    setGlobalError(null);
    setPreview(null);
    setSessionId(null);
    setBusy(false);
  }

  function handleClose() {
    reset();
    onClose();
  }

  async function handlePreview(e: FormEvent) {
    e.preventDefault();
    setAmountError(null);
    setGlobalError(null);

    const parsed = AmountSchema.safeParse(amount);
    if (!parsed.success) {
      setAmountError(parsed.error.issues[0]?.message ?? "Invalid amount");
      return;
    }

    setBusy(true);
    try {
      const resp = await createCreditTopup(parsed.data, newIdempotencyKey());
      setPreview(resp.preview);
      setSessionId(resp.session_id);
    } catch (err) {
      setGlobalError(checkoutErrorMessage(err, "Couldn't prepare the top-up."));
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirm() {
    if (!sessionId) return;
    setGlobalError(null);
    setBusy(true);
    try {
      await confirmCheckout(sessionId);
      reset();
      onSuccess();
      onClose();
    } catch (err) {
      setGlobalError(checkoutErrorMessage(err, "Couldn't complete the top-up."));
    } finally {
      setBusy(false);
    }
  }

  const trapRef = useFocusTrap<HTMLDivElement>(isOpen, handleClose);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4"
      data-testid="add-credits-overlay"
    >
      <div
        ref={trapRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-credits-title"
        className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-lg"
      >
        <h2 id="add-credits-title" className="text-lg font-semibold text-foreground">
          Add credits
        </h2>

        {preview === null ? (
          <form onSubmit={handlePreview} noValidate className="mt-4 flex flex-col gap-4">
            <div className="flex flex-col gap-1">
              <label htmlFor="topup_amount_input" className="text-sm font-medium text-foreground">
                Amount (USD)
              </label>
              <Input
                id="topup_amount_input"
                type="text"
                inputMode="decimal"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                autoComplete="off"
                placeholder="50.00"
              />
              {amountError && (
                <p role="alert" aria-live="polite" className="text-sm text-destructive">
                  {amountError}
                </p>
              )}
            </div>

            {globalError && (
              <p role="alert" aria-live="polite" className="text-sm text-destructive">
                {globalError}
              </p>
            )}

            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={handleClose}>
                Cancel
              </Button>
              <Button type="submit" disabled={busy}>
                {busy ? "Preparing…" : "Review top-up"}
              </Button>
            </div>
          </form>
        ) : (
          <div className="mt-4 flex flex-col gap-4">
            <dl className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 p-4 text-sm">
              <div className="flex items-center justify-between">
                <dt className="text-muted-foreground">Top-up amount</dt>
                <dd className="font-mono tabular-nums text-foreground" data-testid="topup-amount">
                  {formatUsd(preview.amount_usd)}
                </dd>
              </div>
              <div className="flex items-center justify-between">
                <dt className="text-muted-foreground">Balance before</dt>
                <dd className="font-mono tabular-nums text-foreground">{formatUsd(preview.balance_before_usd)}</dd>
              </div>
              <div className="flex items-center justify-between border-t border-border pt-2">
                <dt className="font-medium text-foreground">Balance after</dt>
                <dd
                  className="font-mono tabular-nums font-semibold text-foreground"
                  data-testid="topup-balance-after"
                >
                  {formatUsd(preview.balance_after_preview_usd)}
                </dd>
              </div>
            </dl>

            {globalError && (
              <p role="alert" aria-live="polite" className="text-sm text-destructive">
                {globalError}
              </p>
            )}

            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setPreview(null);
                  setSessionId(null);
                  setGlobalError(null);
                }}
                disabled={busy}
              >
                Back
              </Button>
              <Button type="button" onClick={handleConfirm} disabled={busy}>
                {busy ? "Adding…" : "Confirm top-up"}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
