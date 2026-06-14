"use client";

/**
 * CreateTeamDialog — modal dialog for creating a team (POST /admin/teams).
 *
 * Hand-rolled inline (not the Radix Dialog) so jsdom can interact without
 * showModal(); focus is trapped via useFocusTrap (Escape closes, Tab wraps,
 * focus returns to the opener). Blank/whitespace names are blocked client-side;
 * a 409 from the server surfaces as an inline role="alert" and the dialog stays
 * open (never a silent failure).
 */

import { useState, type FormEvent } from "react";
import { z } from "zod";
import { BffError } from "@/lib/bff-client";
import { Input, Button } from "@/components/ui";
import { useFocusTrap } from "@/lib/use-focus-trap";

const CreateTeamSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Team name is required")
    .max(200, "Team name must be at most 200 characters"),
});

interface CreateTeamDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (name: string) => Promise<void>;
}

export function CreateTeamDialog({ isOpen, onClose, onSubmit }: CreateTeamDialogProps) {
  const [name, setName] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  function handleClose() {
    setName("");
    setNameError(null);
    setGlobalError(null);
    onClose();
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setNameError(null);
    setGlobalError(null);

    const result = CreateTeamSchema.safeParse({ name });
    if (!result.success) {
      setNameError(result.error.issues[0]?.message ?? "Invalid name");
      return;
    }

    setIsSubmitting(true);
    try {
      await onSubmit(name.trim());
      setName("");
      onClose();
    } catch (err) {
      if (err instanceof BffError) {
        if (err.status === 422) setNameError(err.problem.title ?? "Invalid team name");
        else setGlobalError(err.problem.title ?? "Failed to create team");
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4">
      <div
        ref={trapRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-team-title"
        className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-lg"
      >
        <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
          <h2 id="create-team-title" className="text-lg font-semibold text-foreground">
            Create team
          </h2>

          <div className="flex flex-col gap-1">
            <label htmlFor="team_name_input" className="text-sm font-medium text-foreground">
              Team name
            </label>
            <Input
              id="team_name_input"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoComplete="off"
            />
            {nameError && (
              <p role="alert" aria-live="polite" className="text-sm text-destructive">
                {nameError}
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
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Creating…" : "Create"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
