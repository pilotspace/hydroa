"use client";

/**
 * DomainInviteLinkSection — the "Invite your team by link" inline panel on a
 * member/owner-verified Domains-tab row (invite-by-domain-ui TASK.md §3
 * CONTRACT — FROZEN @ v1, M1-M7, R1-R2, R9).
 *
 * Presentation + BFF pass-through ONLY over the FROZEN 6a domain-invite-link
 * gateway core (commit 71641c5, admin endpoints reached via the existing
 * cookie-auth catch-all `/api/gw/admin/domain-invite-links*`). This component
 * owns the mint/revoke mutations; the parent (DomainClaimsSettings) owns the
 * list read and invalidates it via onMinted/onRevoked so a later render (a
 * true reload) never re-shows a token (R9) — the token exists ONLY in this
 * component's own local `justMinted` state, set exclusively from a 201 mint
 * response body and never re-fetched.
 *
 * States: empty | active (no URL) | supersede-confirm | minted-once (URL) |
 * calm error (role="status", never role="alert" — M6/R2).
 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Copy } from "lucide-react";
import { bffPost, bffDelete, BffError } from "@/lib/bff-client";
import { Button } from "@/components/ui";
import type { DomainClaimListItem } from "./DomainClaimsSettings";

export interface ActiveInviteLink {
  id: string;
  domain: string;
  status: "active";
  expires_at: string;
}

export interface MintedInviteLink {
  id: string;
  domain: string;
  token: string;
  status: string;
  expires_at: string;
  created_at: string;
}

export interface DomainInviteLinkSectionProps {
  claim: DomainClaimListItem;
  activeLink: ActiveInviteLink | null;
  onMinted: (link: MintedInviteLink) => void;
  onRevoked: () => void;
}

const LINKS_PATH = "/admin/domain-invite-links";

/** Calm, self-contained error-code -> message map (M6/R2). Any other/unknown
 * code falls back to the upstream problem's own title, never a raw crash. */
const CREATE_ERROR_MESSAGES: Record<string, string> = {
  ERR_DOMAIN_INVITE_NOT_ELIGIBLE:
    "This domain's verification isn't confirmed right now — refresh the page and try again.",
};

function bffCode(err: unknown): string | null {
  if (err instanceof BffError) {
    const code = (err.problem as { code?: unknown }).code;
    if (typeof code === "string") return code;
  }
  return null;
}

function calmCreateMessage(err: unknown): string {
  const code = bffCode(err);
  if (code && code in CREATE_ERROR_MESSAGES) return CREATE_ERROR_MESSAGES[code];
  if (err instanceof BffError) return err.problem.title ?? "Something went wrong. Please try again.";
  return "Something went wrong. Please try again.";
}

/** Fire-and-forget clipboard write — mirrors DomainClaimsSettings' copy idiom;
 * a copy affordance must never crash the console. */
function copyToClipboard(text: string): void {
  try {
    void navigator.clipboard?.writeText(text).catch(() => {});
  } catch {
    // jsdom / permission-denied — the visible <code> value remains selectable.
  }
}

export function DomainInviteLinkSection({
  claim,
  activeLink,
  onMinted,
  onRevoked,
}: DomainInviteLinkSectionProps) {
  const [justMinted, setJustMinted] = useState<MintedInviteLink | null>(null);
  const [showSupersedeConfirm, setShowSupersedeConfirm] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  // SSR-safe origin composition — the gateway returns only the token, never a
  // URL; the shareable link is a plain DERIVED value (never persisted state),
  // computed only on the client (this "use client" component only reaches this
  // branch post-mint, i.e. post-hydration) so it never diverges from a stored
  // copy — there is nothing to resynchronize, so no effect is needed.
  const shareUrl =
    justMinted && typeof window !== "undefined"
      ? `${window.location.origin}/join/${justMinted.token}`
      : null;

  const effectiveLink = justMinted ?? activeLink;

  const createLink = useMutation({
    mutationFn: () => bffPost<MintedInviteLink>(LINKS_PATH, { domain: claim.domain }),
    onSuccess: (link) => {
      setStatusMessage(null);
      setShowSupersedeConfirm(false);
      setJustMinted(link);
      onMinted(link);
    },
    onError: (err) => {
      // M6/R2: the row stays exactly as it was — no crash, no loud alert.
      setStatusMessage(calmCreateMessage(err));
    },
  });

  const revokeLink = useMutation({
    mutationFn: (id: string) => bffDelete(`${LINKS_PATH}/${id}`),
    onSuccess: () => {
      setStatusMessage(null);
      setJustMinted(null);
      onRevoked();
    },
    onError: (err) => {
      setStatusMessage(calmCreateMessage(err));
    },
  });

  return (
    <div
      data-slot="domain-invite-link-section"
      className="flex flex-col gap-3 rounded-lg border border-border bg-muted/20 p-4"
    >
      <div className="flex flex-col gap-1">
        <span className="text-sm font-medium text-foreground">Invite your team by link</span>
        <p className="text-sm text-muted-foreground">
          Anyone with an @{claim.domain} email can join this workspace as a member using this
          shareable link.
        </p>
      </div>

      {showSupersedeConfirm && (
        <div
          data-slot="supersede-confirm"
          className="flex flex-col gap-2 rounded-md border border-warning/40 bg-warning/10 p-3"
        >
          <p role="status" aria-live="polite" className="text-sm text-foreground">
            This replaces the current link — the old URL stops working.
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              size="sm"
              disabled={createLink.isPending}
              onClick={() => createLink.mutate()}
            >
              Confirm
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setShowSupersedeConfirm(false)}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {!showSupersedeConfirm && justMinted && shareUrl && (
        <div data-slot="invite-link-minted" className="flex flex-col gap-2">
          <div className="flex items-start gap-2">
            <code className="block min-w-0 flex-1 break-all rounded-md border border-border bg-muted px-3 py-2 font-mono text-sm text-foreground">
              {shareUrl}
            </code>
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-label="Copy invite link URL"
              onClick={() => copyToClipboard(shareUrl)}
            >
              <Copy className="size-3.5" aria-hidden="true" />
              Copy
            </Button>
          </div>
          <p className="text-sm text-muted-foreground">
            Copy now — you won&apos;t see it again. Expires in 30 days.
          </p>
          <div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={revokeLink.isPending}
              onClick={() => revokeLink.mutate(justMinted.id)}
            >
              Revoke link
            </Button>
          </div>
        </div>
      )}

      {!showSupersedeConfirm && !justMinted && effectiveLink && (
        <div data-slot="invite-link-active" className="flex flex-col gap-2">
          <p className="text-sm text-foreground">
            A link is active — expires {new Date(effectiveLink.expires_at).toLocaleDateString()}.
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setShowSupersedeConfirm(true)}
            >
              Create again
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={revokeLink.isPending}
              onClick={() => revokeLink.mutate(effectiveLink.id)}
            >
              Revoke link
            </Button>
          </div>
        </div>
      )}

      {!showSupersedeConfirm && !justMinted && !effectiveLink && (
        <div data-slot="invite-link-empty">
          <Button
            type="button"
            size="sm"
            disabled={createLink.isPending}
            onClick={() => createLink.mutate()}
          >
            {createLink.isPending ? "Creating…" : "Create invite link"}
          </Button>
        </div>
      )}

      {statusMessage && (
        <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
          {statusMessage}
        </p>
      )}
    </div>
  );
}
