"use client";

/**
 * CreateKeyDialog — modal dialog for creating a new API key
 *
 * Rendered inline (not a native <dialog>) so jsdom tests can interact
 * with it without showModal() support.
 * Tests expect: getByLabelText(/key name/i), getByRole("button", {name:/create/i})
 */

import { useState, FormEvent } from "react";
import { z } from "zod";
import { ApiError } from "@/lib/api-client";
import { Input, Button } from "@/components/ui";
import { useFocusTrap } from "@/lib/use-focus-trap";

const CreateKeySchema = z.object({
  name: z.string().min(1, "Key name is required").max(120, "Key name must be at most 120 characters"),
});

interface CreateKeyDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (name: string) => Promise<void>;
}

export function CreateKeyDialog({ isOpen, onClose, onSubmit }: CreateKeyDialogProps) {
  const [keyName, setKeyName] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  function handleClose() {
    setKeyName("");
    setNameError(null);
    setGlobalError(null);
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
      if (err instanceof ApiError) {
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
