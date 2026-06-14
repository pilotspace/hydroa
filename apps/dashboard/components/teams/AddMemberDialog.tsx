"use client";

/**
 * AddMemberDialog — modal dialog for adding a member BY EMAIL
 * (POST /admin/teams/{id}/members {email, role}).
 *
 * Email resolution is server-side + tenant-scoped (the teams-add-by-email CR);
 * an unknown/cross-tenant email returns 404, surfaced here as the friendly
 * "No user with that email in your tenant" (never the raw gateway title). Role is
 * a native <select> (jsdom-testable + zero-config a11y). Blank email is blocked
 * client-side. Hand-rolled inline dialog with useFocusTrap.
 */

import { useState, type FormEvent } from "react";
import { z } from "zod";
import { BffError } from "@/lib/bff-client";
import { Input, Button } from "@/components/ui";
import { useFocusTrap } from "@/lib/use-focus-trap";
import type { TeamRole } from "./types";

const AddMemberSchema = z.object({
  email: z
    .string()
    .trim()
    .min(1, "Email is required")
    .refine((v) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v), "Enter a valid email"),
});

interface AddMemberDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (email: string, role: TeamRole) => Promise<void>;
}

export function AddMemberDialog({ isOpen, onClose, onSubmit }: AddMemberDialogProps) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<TeamRole>("member");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  function handleClose() {
    setEmail("");
    setRole("member");
    setEmailError(null);
    setGlobalError(null);
    onClose();
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setEmailError(null);
    setGlobalError(null);

    const result = AddMemberSchema.safeParse({ email });
    if (!result.success) {
      setEmailError(result.error.issues[0]?.message ?? "Invalid email");
      return;
    }

    setIsSubmitting(true);
    try {
      await onSubmit(email.trim(), role);
      setEmail("");
      setRole("member");
      onClose();
    } catch (err) {
      if (err instanceof BffError) {
        if (err.status === 404) setGlobalError("No user with that email in your tenant");
        else if (err.status === 422) setEmailError(err.problem.title ?? "Invalid request");
        else setGlobalError(err.problem.title ?? "Failed to add member");
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
        aria-labelledby="add-member-title"
        className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-lg"
      >
        <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
          <h2 id="add-member-title" className="text-lg font-semibold text-foreground">
            Add member
          </h2>

          <div className="flex flex-col gap-1">
            <label htmlFor="member_email_input" className="text-sm font-medium text-foreground">
              Email
            </label>
            <Input
              id="member_email_input"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="off"
            />
            {emailError && (
              <p role="alert" aria-live="polite" className="text-sm text-destructive">
                {emailError}
              </p>
            )}
          </div>

          <div className="flex flex-col gap-1">
            <label htmlFor="member_role_select" className="text-sm font-medium text-foreground">
              Role
            </label>
            <select
              id="member_role_select"
              value={role}
              onChange={(e) => setRole(e.target.value as TeamRole)}
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              <option value="member">Member</option>
              <option value="lead">Lead</option>
            </select>
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
              {isSubmitting ? "Adding…" : "Add"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
