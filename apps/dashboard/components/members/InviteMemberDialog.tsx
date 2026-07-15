"use client";

/**
 * InviteMemberDialog — modal dialog to invite a new member BY EMAIL
 * (POST /admin/invites {email, role}).
 *
 * COPY-LINK delivery (Tin-approved 2026-07-15 — no email infra): on 201 the
 * response's `token` (plaintext, returned ONCE — never retrievable again) is
 * rendered as a copyable accept link `${origin}/invite/${token}` with a static
 * "expires in 7 days" note (the contract's fixed expiry; PendingInvites renders
 * the LIVE per-row countdown from expires_at). The dialog stays open in this
 * success state until the owner/admin dismisses it — closing clears the token
 * from local state so it is never re-shown (it cannot be re-fetched either way).
 *
 * Role select is filtered by assignableRoles(callerRole) (components/members/roles.ts)
 * — the SAME escalation-ceiling policy MembersPage uses for role reassignment,
 * reused rather than duplicated. UI-convenience only; the gateway is authoritative
 * and 403s an over-reach attempt independently.
 *
 * Modal/focus-trap/validation shape mirrors components/teams/AddMemberDialog.tsx.
 */

import { useState, type FormEvent } from "react";
import { z } from "zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { bffPost, BffError } from "@/lib/bff-client";
import { Input, Button } from "@/components/ui";
import { useFocusTrap } from "@/lib/use-focus-trap";
import { assignableRoles, type RoleTier } from "./roles";
import type { InviteCreateResponse } from "./types";
import { PENDING_INVITES_QUERY_KEY } from "./PendingInvites";

const InviteEmailSchema = z.object({
  email: z
    .string()
    .trim()
    .min(1, "Email is required")
    .refine((v) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v), "Enter a valid email"),
});

interface InviteMemberDialogProps {
  isOpen: boolean;
  onClose: () => void;
  /** The authenticated caller's role — filters the role select (UI convenience). */
  callerRole: string;
}

export function InviteMemberDialog({ isOpen, onClose, callerRole }: InviteMemberDialogProps) {
  const queryClient = useQueryClient();
  const roles = assignableRoles(callerRole);

  const [email, setEmail] = useState("");
  const [role, setRole] = useState<RoleTier | "">(roles[0] ?? "");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [created, setCreated] = useState<InviteCreateResponse | null>(null);
  const [copied, setCopied] = useState(false);

  const createInvite = useMutation({
    mutationFn: ({ email, role }: { email: string; role: string }) =>
      bffPost<InviteCreateResponse>("/admin/invites", { email, role }),
  });

  function resetState() {
    setEmail("");
    setRole(roles[0] ?? "");
    setEmailError(null);
    setGlobalError(null);
    setCreated(null);
    setCopied(false);
  }

  function handleClose() {
    resetState();
    onClose();
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setEmailError(null);
    setGlobalError(null);

    const result = InviteEmailSchema.safeParse({ email });
    if (!result.success) {
      setEmailError(result.error.issues[0]?.message ?? "Invalid email");
      return;
    }
    if (!role) {
      setGlobalError("Select a role");
      return;
    }

    try {
      const invite = await createInvite.mutateAsync({ email: email.trim(), role });
      setCreated(invite);
      // The list is refreshed the moment the invite exists — even though the token
      // itself only ever appears here, the row (email/role/expiry) belongs in
      // PendingInvites immediately.
      void queryClient.invalidateQueries({ queryKey: PENDING_INVITES_QUERY_KEY });
    } catch (err) {
      if (err instanceof BffError) {
        if (err.status === 409) setEmailError("An invite or member with this email already exists");
        else if (err.status === 422) setEmailError(err.problem.title ?? "Invalid request");
        else setGlobalError(err.problem.title ?? "Failed to create invite");
      } else {
        setGlobalError("An unexpected error occurred");
      }
    }
  }

  async function handleCopy() {
    if (!created) return;
    const link = `${window.location.origin}/invite/${created.token}`;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API unavailable/denied — the link is still visible for manual copy.
    }
  }

  const trapRef = useFocusTrap<HTMLDivElement>(isOpen, handleClose);

  if (!isOpen) return null;

  const inviteLink = created ? `${window.location.origin}/invite/${created.token}` : "";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4">
      <div
        ref={trapRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="invite-member-title"
        className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-lg"
      >
        {created ? (
          <div className="flex flex-col gap-4">
            <h2 id="invite-member-title" className="text-lg font-semibold text-foreground">
              Invite created
            </h2>
            <p className="text-sm text-muted-foreground">
              Share this one-time link with{" "}
              <strong className="text-foreground">{created.email}</strong>. It expires in 7 days
              and won&apos;t be shown again.
            </p>
            {/* The plaintext token is rendered ONLY in this visible <code> leaf — never
                logged or persisted. It is cleared from state on close (resetState). */}
            <code
              aria-live="polite"
              className="block break-all rounded-md border border-border bg-muted px-3 py-2 font-mono text-sm text-foreground"
            >
              {inviteLink}
            </code>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={handleCopy}>
                {copied ? "Copied!" : "Copy link"}
              </Button>
              <Button type="button" onClick={handleClose}>
                Done
              </Button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
            <h2 id="invite-member-title" className="text-lg font-semibold text-foreground">
              Invite member
            </h2>

            <div className="flex flex-col gap-1">
              <label htmlFor="invite_email_input" className="text-sm font-medium text-foreground">
                Email
              </label>
              <Input
                id="invite_email_input"
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
              <label htmlFor="invite_role_select" className="text-sm font-medium text-foreground">
                Role
              </label>
              <select
                id="invite_role_select"
                value={role}
                onChange={(e) => setRole(e.target.value as RoleTier)}
                className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm capitalize focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
              >
                {roles.map((r) => (
                  <option key={r} value={r} className="capitalize">
                    {r}
                  </option>
                ))}
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
              <Button type="submit" disabled={createInvite.isPending}>
                {createInvite.isPending ? "Sending…" : "Send invite"}
              </Button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
