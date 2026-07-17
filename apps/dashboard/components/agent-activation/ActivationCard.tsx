"use client";

/**
 * ActivationCard — the /activate device-approval surface (device-activate-page §2/§3).
 *
 * Flow: a logged-in member enters (or arrives with a prefilled) user_code, the card
 * PREVIEWS the pending grant's server-known facts (scope, time-to-expiry, the system
 * default budget cap) over the BFF, then Approve / Deny wire to the FROZEN endpoints.
 *
 * SECURITY / a11y invariants:
 *  - the plaintext code travels ONLY in a POST body (bffPost), never a URL.
 *  - EVERY non-previewable outcome (the gateway's uniform 404) collapses to ONE generic
 *    "invalid or has expired" message — the card leaks no distinction (no oracle, M7).
 *  - state is conveyed by the AuthorizationSeal (icon+text, not color) and errors via
 *    role="alert"; the labeled input + visible submit are keyboard-first (M10).
 */

import { useEffect, useState, type FormEvent } from "react";
import { BffError } from "@/lib/bff-client";
import {
  approveDeviceGrant,
  denyDeviceGrant,
  normalizeUserCode,
  previewDeviceGrant,
  type PreviewFacts,
} from "./client";
import { AuthorizationSeal } from "./AuthorizationSeal";
import { Button, Card, CardContent, Input } from "@/components/ui";

type View =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "pending"; code: string; facts: PreviewFacts }
  | { kind: "granted" }
  | { kind: "denied" }
  | { kind: "not_previewable" }
  | { kind: "error" };

const GENERIC_NOT_PREVIEWABLE = "This code is invalid or has expired. Ask the app to show a new code.";

export interface ActivationCardProps {
  initialUserCode?: string;
}

export function ActivationCard({ initialUserCode = "" }: ActivationCardProps) {
  const [code, setCode] = useState(initialUserCode);
  const [view, setView] = useState<View>({ kind: "idle" });
  const [busy, setBusy] = useState(false);

  async function runPreview(rawCode: string): Promise<void> {
    const normalized = normalizeUserCode(rawCode);
    setView({ kind: "loading" });
    try {
      const facts = await previewDeviceGrant(normalized);
      setView({ kind: "pending", code: normalized, facts });
    } catch (err) {
      // Uniform 404 -> single generic message (no oracle). Any other failure -> generic error.
      if (err instanceof BffError && err.status === 404) {
        setView({ kind: "not_previewable" });
      } else {
        setView({ kind: "error" });
      }
    }
  }

  // Auto-preview a prefilled code (arrived via ?user_code= on the page). This is a
  // one-shot sync from an external input (the server-provided prop) on mount — the
  // SSR-safe pattern, mirroring LoginForm's localStorage seed; the rule is over-strict
  // here (it is not a cascading render, it kicks off one fetch → one state transition).
  useEffect(() => {
    if (!initialUserCode) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void runPreview(initialUserCode);
  }, [initialUserCode]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (code.trim() === "") return;
    await runPreview(code);
  }

  async function handleApprove(pendingCode: string): Promise<void> {
    setBusy(true);
    try {
      await approveDeviceGrant(pendingCode);
      setView({ kind: "granted" });
    } catch {
      setView({ kind: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function handleDeny(pendingCode: string): Promise<void> {
    setBusy(true);
    try {
      await denyDeviceGrant(pendingCode);
      setView({ kind: "denied" });
    } catch {
      setView({ kind: "error" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card data-slot="activation-card" className="w-full max-w-md">
      <CardContent className="flex flex-col gap-5 p-6">
        <div className="flex flex-col gap-1.5">
          <h1 className="text-xl font-semibold tracking-tight text-foreground">
            Approve device access
          </h1>
          <p className="text-sm text-muted-foreground">
            Enter the code shown by the app requesting access, then review and approve or deny it.
          </p>
        </div>

        {(view.kind === "idle" ||
          view.kind === "loading" ||
          view.kind === "not_previewable" ||
          view.kind === "error") && (
          <form onSubmit={handleSubmit} className="flex flex-col gap-3" aria-label="Enter device code">
            <div className="flex flex-col gap-1.5">
              <label htmlFor="activate_user_code" className="text-sm font-medium text-foreground">
                Device code
              </label>
              <Input
                id="activate_user_code"
                type="text"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                autoComplete="one-time-code"
                placeholder="BCDF-GHJK"
                aria-describedby={
                  view.kind === "not_previewable" || view.kind === "error"
                    ? "activate_error"
                    : undefined
                }
              />
            </div>

            {view.kind === "not_previewable" && (
              <p id="activate_error" role="alert" className="text-sm text-destructive">
                {GENERIC_NOT_PREVIEWABLE}
              </p>
            )}
            {view.kind === "error" && (
              <p id="activate_error" role="alert" className="text-sm text-destructive">
                Something went wrong. Please try again.
              </p>
            )}

            <Button type="submit" disabled={view.kind === "loading"} className="w-full">
              {view.kind === "loading" ? "Checking…" : "Continue"}
            </Button>
          </form>
        )}

        {view.kind === "pending" && (
          <div className="flex flex-col gap-4" data-slot="preview-facts">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-foreground">Requested access</span>
              <AuthorizationSeal status="pending" />
            </div>

            <dl className="flex flex-col gap-2 text-sm">
              <div className="flex justify-between gap-4">
                <dt className="text-muted-foreground">Scope</dt>
                <dd className="font-mono text-foreground">{view.facts.scope}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-muted-foreground">Expires in</dt>
                <dd className="font-mono text-foreground" aria-live="polite">
                  {view.facts.expires_in}s
                </dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-muted-foreground">Default budget cap</dt>
                <dd className="font-mono text-foreground">
                  ${view.facts.default_budget_usd}
                  <span className="ml-1 text-xs text-muted-foreground">/mo (system default)</span>
                </dd>
              </div>
            </dl>

            <div className="flex gap-3">
              <Button
                type="button"
                variant="outline"
                className="flex-1"
                disabled={busy}
                onClick={() => void handleDeny(view.code)}
              >
                Deny
              </Button>
              <Button
                type="button"
                className="flex-1"
                disabled={busy}
                onClick={() => void handleApprove(view.code)}
              >
                Approve
              </Button>
            </div>
          </div>
        )}

        {view.kind === "granted" && (
          <div className="flex flex-col items-center gap-3" data-slot="granted">
            <AuthorizationSeal status="granted" />
            <p className="text-sm text-muted-foreground">
              Access granted. You can close this page and return to the app.
            </p>
          </div>
        )}

        {view.kind === "denied" && (
          <div className="flex flex-col items-center gap-3" data-slot="denied">
            <AuthorizationSeal status="denied" />
            <p className="text-sm text-muted-foreground">
              Access denied. The app will not receive a token.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
