"use client";

/**
 * MemberVerifyCodeEntry — the inline 6-digit code-entry block (member-verified-
 * code-entry TASK.md §3 CONTRACT — FROZEN @ v1, M4-M12).
 *
 * Presentation + BFF pass-through ONLY: sends `{ code }` to member-verify or
 * `{}` to resend — NEVER an email or domain (M14, the server derives the
 * recipient from the caller's own signup email). Every failure renders a CALM
 * `role="status"` message (never `role="alert"` — the dns-verify-softeners
 * calm-error convention); retryable errors PRESERVE the six segments; only a
 * resend 200 clears them (M11).
 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { bffPost, BffError } from "@/lib/bff-client";
import { Button } from "@/components/ui";
import { OtpInput } from "./OtpInput";

export interface MemberVerifyClaim {
  claim_id: string;
  domain: string;
  status: string;
  member_verified_at: string | null;
  [key: string]: unknown;
}

export interface MemberVerifyCodeEntryProps {
  claimId: string;
  onVerified: (item: MemberVerifyClaim) => void;
}

const CODE_LENGTH = 6;

/** Error-code -> calm message map (§3 CONTRACT item 4 — FROZEN, verbatim copy). */
const ERROR_MESSAGES: Record<string, string> = {
  ERR_MEMBER_VERIFY_CODE_INVALID:
    "That code doesn't match. Double-check the 6 digits from your email and try again.",
  ERR_MEMBER_VERIFY_CODE_EXPIRED:
    "This code has expired. Codes are valid for a short window — request a fresh one.",
  ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS:
    "Too many tries on this code. For your security we've paused it — send a new code to start over.",
  ERR_RATE_LIMITED: "You're going a little fast. Please wait a moment, then try again.",
  ERR_DOMAIN_CLAIM_NOT_PENDING: "This domain is already verified — no code needed.",
  ERR_MEMBER_VERIFY_DOMAIN_MISMATCH:
    "This code was sent to a different email domain than this claim. It can only confirm the domain of your own work email.",
  ERR_AUTH_FORBIDDEN: "You don't have permission to confirm this domain.",
  ERR_DOMAIN_CLAIM_NOT_FOUND:
    "We couldn't find this domain claim — it may have been removed. Refresh and try again.",
  ERR_DOMAIN_GENERIC:
    "Public email domains (like gmail.com) can't be domain-verified. Use your work email.",
  ERR_MEMBER_VERIFY_NOT_ELIGIBLE: "Member verification isn't available for personal accounts.",
};

const FALLBACK_MESSAGE = "Something went wrong. Please try again.";

function bffCode(err: unknown): string | null {
  if (err instanceof BffError) {
    const code = (err.problem as { code?: unknown }).code;
    if (typeof code === "string") return code;
  }
  return null;
}

function calmMessage(err: unknown): string {
  const code = bffCode(err);
  if (code && code in ERROR_MESSAGES) return ERROR_MESSAGES[code];
  return FALLBACK_MESSAGE;
}

export function MemberVerifyCodeEntry({ claimId, onVerified }: MemberVerifyCodeEntryProps) {
  const [segments, setSegments] = useState<string[]>(() => Array(CODE_LENGTH).fill(""));
  const [resetKey, setResetKey] = useState(0);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  const code = segments.join("");
  const complete = segments.length === CODE_LENGTH && segments.every((c) => c !== "");

  const verifyMutation = useMutation({
    mutationFn: (submittedCode: string) =>
      bffPost<MemberVerifyClaim>(`/admin/domain-claims/${claimId}/member-verify`, {
        code: submittedCode,
      }),
    onSuccess: (item) => {
      setStatusMessage(null);
      onVerified(item);
    },
    onError: (err) => {
      // Retryable — the six segments STAY PUT (M11); only a resend 200 clears them.
      setStatusMessage(calmMessage(err));
    },
  });

  const resendMutation = useMutation({
    mutationFn: () => bffPost<MemberVerifyClaim>(`/admin/domain-claims/${claimId}/member-verify/resend`, {}),
    onSuccess: () => {
      setStatusMessage("We sent a fresh code — check your email.");
      setSegments(Array(CODE_LENGTH).fill(""));
      setResetKey((k) => k + 1);
    },
    onError: (err) => {
      // Resend failures (generic domain / not eligible / etc.) do NOT clear segments.
      setStatusMessage(calmMessage(err));
    },
  });

  return (
    <div
      data-slot="member-verify-code-entry"
      className="flex flex-col gap-3 rounded-lg border border-accent-soft-border bg-accent-soft p-4"
    >
      <div className="flex flex-col gap-1">
        <span className="text-sm font-medium text-foreground">Confirm you work at this domain</span>
        <p className="text-sm text-muted-foreground">
          Enter the 6-digit code we emailed to your own account address to confirm you belong to
          this domain — this joins you as a member-verified teammate.
        </p>
      </div>

      <OtpInput
        key={resetKey}
        length={CODE_LENGTH}
        disabled={verifyMutation.isPending}
        onChange={setSegments}
      />

      {statusMessage && (
        <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
          {statusMessage}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          disabled={!complete || verifyMutation.isPending}
          onClick={() => verifyMutation.mutate(code)}
        >
          Confirm
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={resendMutation.isPending}
          onClick={() => resendMutation.mutate()}
        >
          Resend code
        </Button>
      </div>
    </div>
  );
}
