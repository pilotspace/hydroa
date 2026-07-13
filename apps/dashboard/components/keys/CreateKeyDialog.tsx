"use client";

/**
 * CreateKeyDialog — modal dialog for creating a new API key
 *
 * Rendered inline (not a native <dialog>) so jsdom tests can interact
 * with it without showModal() support.
 * Tests expect: getByLabelText(/key name/i), getByRole("button", {name:/create/i})
 */

import { useState, useEffect, FormEvent } from "react";
import { z } from "zod";
import { bffGet, BffError } from "@/lib/bff-client";
import { Input, Button } from "@/components/ui";
import { useFocusTrap } from "@/lib/use-focus-trap";
import { TierSelector, type Tier } from "./TierSelector";

const CreateKeySchema = z.object({
  name: z.string().min(1, "Key name is required").max(120, "Key name must be at most 120 characters"),
});

/** GET /admin/service-tiers — service-tiers TASK.md §3 M11, FROZEN, effective
 * (override-or-seed) shape. residency-tiers-ui TASK.md §3 "DECIDED at freeze review":
 * this is the REAL route/shape — NOT the earlier-drafted {entries:[...]} shape. */
interface ServiceTiersEffective {
  default_tier: string;
  priority_markup_pct: string;
}

interface CreateKeyDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (name: string) => Promise<void>;
  /** Fires on every tier change (default "standard" on close/reset). CreateKeyDialog
   * owns the tier UI; the caller (KeysPage) owns assembling the POST body — this keeps
   * onSubmit's signature untouched (a pre-existing, out-of-scope suite asserts it is
   * called with exactly one argument). */
  onTierChange?: (tier: Tier) => void;
}

export function CreateKeyDialog({ isOpen, onClose, onSubmit, onTierChange }: CreateKeyDialogProps) {
  const [keyName, setKeyName] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [tier, setTier] = useState<Tier>("standard");
  const [priceDelta, setPriceDelta] = useState<string | null>(null);

  // M10: read GET /admin/service-tiers ONCE per dialog open. A plain fetch (not
  // react-query) — CreateKeyDialog is rendered standalone with no QueryClientProvider
  // ancestor in several pre-existing, out-of-scope tests. R4: on 404/5xx/network error,
  // no retry/poll — priceDelta simply stays null ("Pricing pending"), never an
  // ErrorState (expected degrade, not a fault).
  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    bffGet<ServiceTiersEffective>("/admin/service-tiers")
      .then((resp) => {
        if (cancelled) return;
        const pct = Number(resp.priority_markup_pct);
        setPriceDelta(Number.isFinite(pct) ? `+${pct}% on requests using this key` : null);
      })
      .catch(() => {
        if (!cancelled) setPriceDelta(null);
      });
    return () => {
      cancelled = true;
    };
  }, [isOpen]);

  function handleTierChange(next: Tier) {
    setTier(next);
    onTierChange?.(next);
  }

  function handleClose() {
    setKeyName("");
    setNameError(null);
    setGlobalError(null);
    setTier("standard");
    onTierChange?.("standard");
    onClose();
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setNameError(null);
    setGlobalError(null);

    const result = CreateKeySchema.safeParse({ name: keyName });
    if (!result.success) {
      setNameError(result.error.issues[0]?.message ?? "Invalid name");
      return;
    }

    setIsSubmitting(true);
    try {
      await onSubmit(keyName.trim());
      setKeyName("");
      onClose();
    } catch (err) {
      if (err instanceof BffError) {
        if (err.status === 422) {
          setNameError(err.problem.title ?? "Invalid key name");
        } else {
          setGlobalError(err.problem.title ?? "Failed to create key");
        }
      } else {
        setGlobalError("An unexpected error occurred");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  const trapRef = useFocusTrap<HTMLDivElement>(isOpen, handleClose);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4"
      data-testid="create-key-overlay"
    >
      <div
        ref={trapRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-key-title"
        className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-lg"
      >
        <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
          <h2 id="create-key-title" className="text-lg font-semibold text-foreground">
            Create API Key
          </h2>

          <div className="flex flex-col gap-1">
            <label
              htmlFor="key_name_input"
              className="text-sm font-medium text-foreground"
            >
              Key Name
            </label>
            <Input
              id="key_name_input"
              type="text"
              value={keyName}
              onChange={(e) => setKeyName(e.target.value)}
              autoComplete="off"
            />
            {nameError && (
              <p role="alert" aria-live="polite" className="text-sm text-destructive">
                {nameError}
              </p>
            )}
          </div>

          <TierSelector value={tier} onChange={handleTierChange} priceDelta={priceDelta} />

          {globalError && (
            <p role="alert" aria-live="polite" className="text-sm text-destructive">
              {globalError}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={handleClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Creating…" : "Create"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
