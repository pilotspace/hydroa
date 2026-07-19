"use client";

/**
 * DomainStatusSeal — the ONE status marker for a tenant domain claim
 * (domain-claims-console TASK.md §3 CONTRACT — FROZEN @ v1, M2).
 *
 * Reuses the immutability-marker idiom of InvoiceStatusSeal / BundleEvidenceSeal
 * verbatim: a success-tinted Badge + a lock icon + sr-only copy asserting domain
 * OWNERSHIP for the sealed (verified) branch — never a generic badge.
 *
 * Seal states are DERIVED, not a 1:1 map of the persisted `status` (which only
 * ever holds "pending" | "verified" — frozen ClaimStatus):
 *   status == "verified"                          -> "verified"  (sealed branch)
 *   status == "pending" && expires_at > now       -> "pending"   (warning)
 *   status == "pending" && expires_at <= now      -> "expired"   (destructive,
 *     client-DERIVED and advisory — the API authoritatively enforces expiry via
 *     ERR_DOMAIN_CLAIM_EXPIRED on verify; clock skew makes this a hint, not truth)
 * A failed verify is EPHEMERAL (the claim stays pending server-side) — it is an
 * inline alert in the console, NEVER a seal state (R2).
 */

import { Lock } from "lucide-react";
import { Badge } from "@/components/ui";

export type DomainSealState = "verified" | "pending" | "expired";

export interface DomainClaimSealInput {
  status: string;
  expires_at: string;
}

/** Pure derivation of the seal state from the frozen claim shape (§3 sealState). */
export function sealState(claim: DomainClaimSealInput, now: Date = new Date()): DomainSealState {
  if (claim.status === "verified") return "verified";
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
