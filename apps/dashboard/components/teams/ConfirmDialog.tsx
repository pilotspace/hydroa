"use client";

/**
 * ConfirmDialog — a hand-rolled destructive-action confirmation (delete team /
 * remove member). Runs onConfirm() (an async mutation); a failure surfaces as an
 * inline role="alert" and the dialog stays open. Focus is trapped (Escape closes,
 * Tab wraps, focus returns). aria-label carries the action so screen readers
 * announce the dialog without a visible-id dependency.
 */

import { useId, useState } from "react";
import { BffError } from "@/lib/bff-client";
import { Button } from "@/components/ui";
import { useFocusTrap } from "@/lib/use-focus-trap";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel: string;
  onConfirm: () => Promise<void>;
  onClose: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const titleId = useId();
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  function handleClose() {
    setError(null);
    onClose();
  }

  async function handleConfirm() {
    setError(null);
    setIsSubmitting(true);
    try {
      await onConfirm();
      onClose();
    } catch (err) {
      if (err instanceof BffError) setError(err.problem.title ?? "Action failed");
      else setError("An unexpected error occurred");
    } finally {
      setIsSubmitting(false);
    }
  }

  const trapRef = useFocusTrap<HTMLDivElement>(open, handleClose);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4">
      <div
        ref={trapRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-lg"
      >
        <div className="flex flex-col gap-4">
          <h2 id={titleId} className="text-lg font-semibold text-foreground">
            {title}
          </h2>
          {description && <p className="text-sm text-muted-foreground">{description}</p>}
          {error && (
            <p role="alert" aria-live="polite" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={handleClose} disabled={isSubmitting}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={handleConfirm}
              disabled={isSubmitting}
            >
              {isSubmitting ? "Working…" : confirmLabel}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
