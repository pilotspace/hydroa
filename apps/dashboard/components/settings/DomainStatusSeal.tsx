"use client";

/**
 * DomainStatusSeal — the ONE status marker for a tenant domain claim
 * (domain-claims-console TASK.md §3 CONTRACT — FROZEN @ v1, M2; extended by
 * member-verified-code-entry TASK.md §3 — FROZEN @ v1 with the rung-aware climb).
 *
 * Reuses the immutability-marker idiom of InvoiceStatusSeal / BundleEvidenceSeal
 * verbatim: a success-tinted Badge + a lock icon + sr-only copy asserting domain
 * OWNERSHIP for the sealed (verified) branch — never a generic badge.
 *
 * Seal states are DERIVED, not a 1:1 map of the persisted `status` (which only
 * ever holds "pending" | "verified" — frozen ClaimStatus). Ordered derivation
 * (first match wins — member-verified-code-entry §3 item 2, FROZEN):
 *   status == "verified"                          -> "verified"        (Owner; wins over all)
 *   member_verified_at != null                     -> "member-verified" (⚠ wins over expired + pending —
 *     member_verified_at is durable server truth; expires_at governs only the OPTIONAL DNS/Owner
 *     climb window, so an elapsed DNS window must never visually downgrade a real member)
 *   expires_at > now                                -> "pending"        (warning)
 *   otherwise (incl. unparseable expires_at -> NaN) -> "expired"        (destructive, client-DERIVED
 *     and advisory — the API authoritatively enforces expiry via ERR_DOMAIN_CLAIM_EXPIRED on verify)
 * A failed verify is EPHEMERAL (the claim stays pending server-side) — it is an
 * inline alert in the console, NEVER a seal state (R2).
 */

import { Lock, User } from "lucide-react";
import { Badge } from "@/components/ui";

export type DomainSealState = "verified" | "member-verified" | "pending" | "expired";

export interface DomainClaimSealInput {
  status: string;
  expires_at: string;
  // ADDITIVE (member-verified-code-entry §3, FROZEN): the rung-1 member trust marker.
  member_verified_at?: string | null;
}

/** Pure derivation of the seal state from the frozen claim shape (§3 sealState). */
export function sealState(claim: DomainClaimSealInput, now: Date = new Date()): DomainSealState {
  if (claim.status === "verified") return "verified";
  if (claim.member_verified_at != null) return "member-verified";
  // An unparseable expires_at yields NaN -> the comparison is false -> "expired":
  // failing toward the closed/action-needed state, never a falsely-reassuring pending.
  return new Date(claim.expires_at).getTime() > now.getTime() ? "pending" : "expired";
}

export interface DomainStatusSealProps {
  claim: DomainClaimSealInput;
}

export function DomainStatusSeal({ claim }: DomainStatusSealProps) {
  const state = sealState(claim);

  if (state === "verified") {
    return (
      <Badge variant="success" className="gap-1" data-slot="domain-status-seal">
        <Lock className="size-3" aria-hidden="true" />
        Verified
        <span className="sr-only"> — domain ownership confirmed; this claim is sealed</span>
      </Badge>
    );
  }

  if (state === "member-verified") {
    return (
      <Badge
        variant="outline"
        className="gap-1 border-accent-soft-border bg-accent-soft font-mono tabular-nums text-accent-soft-foreground"
        data-slot="domain-status-seal"
      >
        <User className="size-3" aria-hidden="true" />
        Member-verified
        <span className="sr-only">
          {" "}
          — a teammate confirmed membership of this domain by mailbox code; domain ownership not yet proven
        </span>
      </Badge>
    );
  }

  if (state === "pending") {
    return (
      <Badge variant="warning" data-slot="domain-status-seal">
        Pending DNS
      </Badge>
    );
  }

  return (
    <Badge variant="destructive" data-slot="domain-status-seal">
      Expired
    </Badge>
  );
}
