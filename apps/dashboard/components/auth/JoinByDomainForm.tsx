"use client";

/**
 * JoinByDomainForm — the public (unauthenticated) two-phase domain-invite-link
 * redeem form, rendered by app/(auth)/join/[token]/page.tsx (invite-by-domain-ui
 * TASK.md §3 CONTRACT — FROZEN @ v1, M8-M12, R3-R8).
 *
 * Phases: "email" -> "code" -> "success". No GET preview call (the frozen 6a
 * redeem has no preview endpoint) — the form only ever POSTs what the
 * corresponding gateway endpoint takes:
 *   1. POST /api/auth/join/{token}          {email}                 -> 202
 *   2. POST /api/auth/join/{token}/verify    {email, code, password} -> 201
 *      { ok: true, session: boolean }
 * On session:true -> router.push("/app") (the BFF has already set the httpOnly
 * session cookie). On session:false (a rare chained-login failure — the
 * account/membership was still created, R8) -> router.push to /login with a
 * "you've joined" hint; the user is never stranded. Every other error maps to a
 * calm, self-contained `role="status"` message (never a countdown — the BFF
 * drops Retry-After) except a weak password, which is an inline field error.
 *
 * The password lives in this component's state ONLY for the two POSTs; it is
 * never persisted, logged, or placed in a query string.
 */

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { z } from "zod";
import { Button, Card, CardContent, Input } from "@/components/ui";
import { OtpInput } from "@/components/settings/OtpInput";
import { bffAuthPost, BffError } from "@/lib/bff-client";

export interface JoinByDomainFormProps {
  token: string;
}

type Phase = "email" | "code" | "success";

interface RedeemVerifyResponse {
  ok: boolean;
  session: boolean;
}

const EmailSchema = z.object({
  email: z.string().min(1, "Email is required").max(320, "Email is too long"),
});

const PasswordSchema = z.object({
  password: z.string().min(10, "Must be at least 10 characters"),
});

const CODE_LENGTH = 6;

// Phase-1 (redeem) calm-error map — self-contained, never a countdown (the BFF
// drops Retry-After). Any other/unknown code falls back to the upstream title.
const PHASE1_ERROR_MESSAGES: Record<string, string> = {
  ERR_DOMAIN_INVITE_DOMAIN_MISMATCH:
    "This invite link only works with your work email address for this domain.",
  ERR_INVITE_NOT_FOUND: "This invite link is invalid.",
  ERR_DOMAIN_INVITE_LINK_INACTIVE: "This invite link is no longer active.",
  ERR_INVITE_EXPIRED: "This invite link has expired.",
  ERR_RATE_LIMITED: "You're going a little fast. Please wait a moment, then try again.",
};

// Phase-2 (verify) calm-error map — ERR_AUTH_PASSWORD_WEAK is handled separately
// (inline under the password field, R6), never through this status map.
const PHASE2_ERROR_MESSAGES: Record<string, string> = {
  ERR_MEMBER_VERIFY_CODE_INVALID:
    "That code doesn't match. Double-check the 6 digits from your email and try again.",
  ERR_MEMBER_VERIFY_CODE_EXPIRED:
    "This code has expired. Codes are valid for a short window — send a new one below.",
  ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS:
    "Too many tries on this code. For your security we've paused it — send a new code to start over.",
  ERR_TENANT_EMAIL_TAKEN: "An account with this email already exists.",
  ERR_PLAN_SEAT_CAP_EXCEEDED: "This workspace has reached its seat limit — ask an admin for help.",
  ERR_DOMAIN_INVITE_DOMAIN_MISMATCH:
    "This invite link only works with your work email address for this domain.",
  ERR_DOMAIN_INVITE_LINK_INACTIVE: "This invite link is no longer active.",
  ERR_INVITE_EXPIRED: "This invite link has expired.",
  ERR_INVITE_NOT_FOUND: "This invite link is invalid.",
  ERR_RATE_LIMITED: "You're going a little fast. Please wait a moment, then try again.",
};

const FALLBACK_MESSAGE = "Something went wrong. Please try again.";

function bffCode(err: unknown): string | null {
  if (err instanceof BffError) {
    const code = (err.problem as { code?: unknown }).code;
    if (typeof code === "string") return code;
  }
  return null;
}

function calmMessage(err: unknown, table: Record<string, string>): string {
  const code = bffCode(err);
  if (code && code in table) return table[code];
  if (err instanceof BffError) return err.problem.title ?? FALLBACK_MESSAGE;
  return FALLBACK_MESSAGE;
}

export function JoinByDomainForm({ token }: JoinByDomainFormProps) {
  const router = useRouter();

  const [phase, setPhase] = useState<Phase>("email");
  const [email, setEmail] = useState("");
  const [emailError, setEmailError] = useState<string | null>(null);

  const [segments, setSegments] = useState<string[]>(() => Array(CODE_LENGTH).fill(""));
  const [resetKey, setResetKey] = useState(0);
  const [password, setPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);

  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [isSubmittingPhase1, setIsSubmittingPhase1] = useState(false);
  const [isSubmittingPhase2, setIsSubmittingPhase2] = useState(false);
  const [isResending, setIsResending] = useState(false);

  const code = segments.join("");
  const codeComplete = segments.length === CODE_LENGTH && segments.every((s) => s !== "");
  const domainPart = email.split("@")[1]?.trim() || "your team";

  async function requestCode(targetEmail: string): Promise<boolean> {
    try {
      await bffAuthPost(`join/${encodeURIComponent(token)}`, { email: targetEmail });
      return true;
    } catch (err) {
      setStatusMessage(calmMessage(err, PHASE1_ERROR_MESSAGES));
      return false;
    }
  }

  async function handlePhase1(e: FormEvent) {
    e.preventDefault();
    setEmailError(null);
    setStatusMessage(null);

    const trimmed = email.trim();
    const parsed = EmailSchema.safeParse({ email: trimmed });
    if (!parsed.success) {
      setEmailError(parsed.error.issues[0]?.message ?? "Invalid email");
      return;
    }

    setIsSubmittingPhase1(true);
    try {
      const ok = await requestCode(trimmed);
      if (ok) {
        setEmail(trimmed);
        setPhase("code");
      }
    } finally {
      setIsSubmittingPhase1(false);
    }
  }

  async function handlePhase2(e: FormEvent) {
    e.preventDefault();
    setPasswordError(null);
    setStatusMessage(null);

    const parsed = PasswordSchema.safeParse({ password });
    if (!parsed.success) {
      setPasswordError(parsed.error.issues[0]?.message ?? "Invalid password");
      return;
    }

    setIsSubmittingPhase2(true);
    try {
      const resp = await bffAuthPost<RedeemVerifyResponse>(
        `join/${encodeURIComponent(token)}/verify`,
        { email, code, password },
      );

      if (resp.session) {
        setPhase("success");
        router.push("/app");
      } else {
        // R8 — the account/membership was created; the chained login just
        // failed. Never strand the user: hand off to /login with a hint.
        router.push(`/login?joined=${encodeURIComponent(domainPart)}`);
      }
    } catch (err) {
      const errCode = bffCode(err);
      if (errCode === "ERR_AUTH_PASSWORD_WEAK") {
        setPasswordError(
          "Choose a stronger password — at least 10 characters, avoid common words.",
        );
        return;
      }
      setStatusMessage(calmMessage(err, PHASE2_ERROR_MESSAGES));
    } finally {
      setIsSubmittingPhase2(false);
    }
  }

  async function handleResend() {
    setIsResending(true);
    setStatusMessage(null);
    try {
      const ok = await requestCode(email);
      if (ok) {
        setStatusMessage("We sent a fresh code — check your email.");
        setSegments(Array(CODE_LENGTH).fill(""));
        setResetKey((k) => k + 1);
      }
    } finally {
      setIsResending(false);
    }
  }

  if (phase === "email") {
    return (
      <form onSubmit={handlePhase1} noValidate aria-label="Join by domain — email">
        <Card data-slot="auth-card">
          <CardContent className="flex flex-col gap-4 p-6">
            <p className="text-sm text-muted-foreground">
              Enter your work email to join your team&apos;s workspace.
            </p>

            <div className="flex flex-col gap-1.5">
              <label htmlFor="join_email" className="text-sm font-medium text-foreground">
                Work email
              </label>
              <Input
                id="join_email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
              />
              {emailError && (
                <p role="alert" aria-live="polite" className="text-sm text-destructive">
                  {emailError}
                </p>
              )}
            </div>

            {statusMessage && (
              <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
                {statusMessage}
              </p>
            )}

            <Button type="submit" disabled={isSubmittingPhase1} className="w-full">
              {isSubmittingPhase1 ? "Sending…" : "Send me a code"}
            </Button>
          </CardContent>
        </Card>
      </form>
    );
  }

  if (phase === "code") {
    return (
      <form onSubmit={handlePhase2} noValidate aria-label="Join by domain — code and password">
        <Card data-slot="auth-card">
          <CardContent className="flex flex-col gap-4 p-6">
            <p className="text-sm text-muted-foreground">
              We sent a 6-digit code to <strong className="text-foreground">{email}</strong>. Enter
              it below with a password to finish joining.
            </p>

            <OtpInput
              key={resetKey}
              length={CODE_LENGTH}
              disabled={isSubmittingPhase2}
              onChange={setSegments}
            />

            <div className="flex flex-col gap-1.5">
              <label htmlFor="join_password" className="text-sm font-medium text-foreground">
                Set a password
              </label>
              <Input
                id="join_password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
              />
              <p className="text-xs text-muted-foreground">Must be at least 10 characters.</p>
              {passwordError && (
                <p role="alert" aria-live="polite" className="text-sm text-destructive">
                  {passwordError}
                </p>
              )}
            </div>

            {statusMessage && (
              <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
                {statusMessage}
              </p>
            )}

            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" disabled={isSubmittingPhase2 || !codeComplete}>
                {isSubmittingPhase2 ? "Joining…" : `Join ${domainPart}`}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={isResending}
                onClick={() => void handleResend()}
              >
                Resend code
              </Button>
            </div>
          </CardContent>
        </Card>
      </form>
    );
  }

  // phase === "success" — router.push("/app") already fired; a brief bridge
  // state so nothing renders blank while the navigation completes.
  return (
    <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
      You&apos;re in! Redirecting…
    </p>
  );
}
