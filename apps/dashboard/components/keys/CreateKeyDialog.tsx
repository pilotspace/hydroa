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

  if (!isOpen) return null;

  return (
    <div role="dialog" aria-modal="true" aria-label="Create API key">
      <form onSubmit={handleSubmit} noValidate>
        <h2>Create API Key</h2>

        <div>
          <label htmlFor="key_name_input">Key Name</label>
          <input
            id="key_name_input"
            type="text"
            value={keyName}
            onChange={(e) => setKeyName(e.target.value)}
            autoComplete="off"
          />
          {nameError && (
            <p role="alert" aria-live="polite">{nameError}</p>
          )}
        </div>

        {globalError && (
          <p role="alert" aria-live="polite">{globalError}</p>
        )}

        <div>
          <button type="button" onClick={handleClose}>
            Cancel
          </button>
          <button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </div>
  );
}
