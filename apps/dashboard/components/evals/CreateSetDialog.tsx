"use client";

/**
 * CreateSetDialog — evals-console TASK.md §3 CONTRACT (authoring surface). Mirrors
 * CreateTeamDialog.tsx's own convention EXACTLY: hand-rolled inline overlay (not the
 * Radix ui/dialog.tsx primitive, so jsdom can interact without showModal() support),
 * useFocusTrap for Escape/Tab-wrap/focus-return, a Zod schema for the one client-side
 * validated input (name), and a server-rejection inline role="alert" that keeps the
 * dialog open with every field preserved.
 */

import { useState, type FormEvent } from "react";
import { z } from "zod";
import { bffPost, BffError } from "@/lib/bff-client";
import { Input, Textarea, Button } from "@/components/ui";
import { useFocusTrap } from "@/lib/use-focus-trap";
import type { EvalSetSummary } from "./types";

const CreateSetSchema = z.object({
  name: z.string().trim().min(1, "Name is required").max(200, "Name must be at most 200 characters"),
  description: z.string().trim().max(2000, "Description must be at most 2000 characters").optional(),
});

export interface CreateSetDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: (set: EvalSetSummary) => void;
}

export function CreateSetDialog({ open, onClose, onCreated }: CreateSetDialogProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  function reset() {
    setName("");
    setDescription("");
    setNameError(null);
    setGlobalError(null);
  }

  function handleClose() {
    reset();
    onClose();
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setNameError(null);
    setGlobalError(null);

    const result = CreateSetSchema.safeParse({ name, description: description.trim() === "" ? undefined : description });
    if (!result.success) {
      setNameError(result.error.issues[0]?.message ?? "Invalid input");
      return;
    }

    setIsSubmitting(true);
    try {
      const created = await bffPost<EvalSetSummary>("/admin/evals/sets", result.data);
      onCreated(created);
      reset();
      onClose();
    } catch (err) {
      if (err instanceof BffError) setGlobalError(err.problem.title ?? "Failed to create eval set");
      else setGlobalError("An unexpected error occurred");
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
        aria-labelledby="create-eval-set-title"
        className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-lg"
      >
        <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
          <h2 id="create-eval-set-title" className="text-lg font-semibold text-foreground">
            New eval set
          </h2>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="eval-set-name-input" className="text-sm font-medium text-foreground">
              Name
            </label>
            <Input
              id="eval-set-name-input"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoComplete="off"
              aria-invalid={nameError ? true : undefined}
              aria-describedby={nameError ? "eval-set-name-error" : undefined}
            />
            {nameError ? (
              <p id="eval-set-name-error" role="alert" aria-live="polite" className="text-sm text-destructive">
                {nameError}
              </p>
            ) : null}
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="eval-set-description-input" className="text-sm font-medium text-foreground">
              Description
            </label>
            <Textarea
              id="eval-set-description-input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional"
              rows={3}
            />
          </div>

          {globalError ? (
            <p role="alert" aria-live="polite" className="text-sm text-destructive">
              {globalError}
            </p>
          ) : null}

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
