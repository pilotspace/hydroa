"use client";

/**
 * UpgradePlanDialog — self-serve plan upgrade (self-serve-checkout TASK.md §3, M8/M9).
 *
 * Mirrors CreateKeyDialog (inline modal, focus trap, checkout-token error mapping) and the
 * two-phase input→preview→confirm flow of AddCreditsDialog so the user sees SERVER-computed
 * price math (current base, target base, delta, "immediate") before the mutation.
 *
 * Pure/prop-driven by design: the target-plan menu is supplied by the caller as
 * `availablePlans`. The frozen §3 contract intentionally scopes NO tenant-facing plans-list
 * read (only GET /admin/plan = the CURRENT plan, and superadmin GET /admin/platform/plans);
 * sourcing the selectable upgrade targets live is a follow-up read the milestone left open.
 * When `availablePlans` is empty the dialog degrades to an honest "no options" state instead
 * of shipping a broken POST.
 */

import { useState } from "react";
import { Button } from "@/components/ui";
import { useFocusTrap } from "@/lib/use-focus-trap";
import { formatUsd } from "@/lib/format";
import {
  createPlanUpgrade,
  confirmCheckout,
  checkoutErrorMessage,
  newIdempotencyKey,
  type PlanUpgradePreview,
} from "@/lib/checkout";

export interface UpgradePlanOption {
  id: string;
  displayName: string;
}

interface UpgradePlanDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
  availablePlans: UpgradePlanOption[];
}

export function UpgradePlanDialog({ isOpen, onClose, onSuccess, availablePlans }: UpgradePlanDialogProps) {
  const [selectedId, setSelectedId] = useState("");
  const [selectError, setSelectError] = useState<string | null>(null);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [preview, setPreview] = useState<PlanUpgradePreview | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function reset() {
    setSelectedId("");
    setSelectError(null);
    setGlobalError(null);
    setPreview(null);
    setSessionId(null);
    setBusy(false);
  }

  function handleClose() {
    reset();
    onClose();
  }

  async function handlePreview() {
    setSelectError(null);
    setGlobalError(null);
    if (!selectedId) {
      setSelectError("Choose a plan to continue");
      return;
    }
    setBusy(true);
    try {
      const resp = await createPlanUpgrade(selectedId, newIdempotencyKey());
      setPreview(resp.preview);
      setSessionId(resp.session_id);
    } catch (err) {
      setGlobalError(checkoutErrorMessage(err, "Couldn't prepare the upgrade."));
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
      setGlobalError(checkoutErrorMessage(err, "Couldn't complete the upgrade."));
    } finally {
      setBusy(false);
    }
  }

  const trapRef = useFocusTrap<HTMLDivElement>(isOpen, handleClose);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4"
      data-testid="upgrade-plan-overlay"
    >
      <div
        ref={trapRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="upgrade-plan-title"
        className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-lg"
      >
        <h2 id="upgrade-plan-title" className="text-lg font-semibold text-foreground">
          Upgrade plan
        </h2>

        {availablePlans.length === 0 ? (
          <div className="mt-4 flex flex-col gap-4">
            <p className="text-sm text-muted-foreground">
              No upgrade options are available right now. Contact your platform operator if you
              expected to see a plan here.
            </p>
            <div className="flex justify-end">
              <Button type="button" variant="outline" onClick={handleClose}>
                Close
              </Button>
            </div>
          </div>
        ) : preview === null ? (
          <div className="mt-4 flex flex-col gap-4">
            <div className="flex flex-col gap-1">
              <label htmlFor="upgrade_plan_select" className="text-sm font-medium text-foreground">
                Target plan
              </label>
              <select
                id="upgrade_plan_select"
                value={selectedId}
                onChange={(e) => setSelectedId(e.target.value)}
                className="rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <option value="">Select a plan…</option>
                {availablePlans.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.displayName}
                  </option>
                ))}
              </select>
              {selectError && (
                <p role="alert" aria-live="polite" className="text-sm text-destructive">
                  {selectError}
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
              <Button type="button" onClick={handlePreview} disabled={busy}>
                {busy ? "Preparing…" : "Review upgrade"}
              </Button>
            </div>
          </div>
        ) : (
          <div className="mt-4 flex flex-col gap-4">
            <dl className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 p-4 text-sm">
              <div className="flex items-center justify-between">
                <dt className="text-muted-foreground">Current plan</dt>
                <dd className="text-foreground" data-testid="upgrade-current-plan">
                  {preview.current_plan ?? "No plan"} · {formatUsd(preview.current_base_usd)}/mo
                </dd>
              </div>
              <div className="flex items-center justify-between">
                <dt className="text-muted-foreground">New plan</dt>
                <dd className="text-foreground" data-testid="upgrade-target-plan">
                  {preview.target_plan} · {formatUsd(preview.target_base_usd)}/mo
                </dd>
              </div>
              <div className="flex items-center justify-between border-t border-border pt-2">
                <dt className="font-medium text-foreground">Monthly change</dt>
                <dd
                  className="font-mono tabular-nums font-semibold text-foreground"
                  data-testid="upgrade-delta"
                >
                  {formatUsd(preview.delta_usd)}
                </dd>
              </div>
              <p className="text-xs text-muted-foreground">Takes effect {preview.effective}.</p>
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
                {busy ? "Upgrading…" : "Confirm upgrade"}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
