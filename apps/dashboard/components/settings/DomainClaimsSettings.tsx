"use client";

/**
 * DomainClaimsSettings — the "Domains" tab of /settings (OWNER-only console;
 * domain-claims-console TASK.md §3 CONTRACT — FROZEN @ v1).
 *
 * Pure pass-through over the FROZEN gateway /admin/domain-claims API via the
 * authenticated BFF proxy (bffGet/bffPost/bffDelete -> /api/gw/*): list (M1),
 * create -> inline DNS-TXT challenge with per-field copy (M3), verify-now with
 * distinct 422-mismatch vs 503-lookup alerts (M4), revoke behind a confirm (M5).
 * All authorization + DNS verification stays SERVER-side (R4) — a non-owner 403
 * ERR_AUTH_FORBIDDEN renders ErrorState, never a broken form (R1, the
 * SamlSettings precedent). A failed verify NEVER flips the seal — the row's
 * cache entry is mutated ONLY from the verify SUCCESS response (R2).
 */

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Copy } from "lucide-react";
import { bffGet, bffPost, bffDelete, BffError } from "@/lib/bff-client";
import {
  Button,
  Input,
  Loading,
  Empty,
  ErrorState,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui";
import { DomainStatusSeal, sealState } from "./DomainStatusSeal";

interface DomainClaimListItem {
  claim_id: string;
  domain: string;
  status: "pending" | "verified";
  dns_record_name: string;
  dns_record_value: string;
  expires_at: string;
  verified_at: string | null;
}

interface DomainClaimListResponse {
  claims: DomainClaimListItem[];
}

interface DomainClaimCreateResponse {
  claim_id: string;
  domain: string;
  status: "pending" | "verified";
  dns_record_type: string;
  dns_record_name: string;
  dns_record_value: string;
  expires_at: string;
}

interface DomainClaimVerifyResponse {
  claim_id: string;
  domain: string;
  status: "pending" | "verified";
  verified_at: string | null;
}

const CLAIMS_KEY = ["admin-domain-claims"] as const;
const CLAIMS_PATH = "/admin/domain-claims";

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) {
    return err.problem.title ?? "An error occurred";
  }
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

function bffCode(err: unknown): string | null {
  if (err instanceof BffError) {
    const code = (err.problem as { code?: unknown }).code;
    if (typeof code === "string") return code;
  }
  return null;
}

/** Fire-and-forget clipboard write — a copy affordance must never crash the console. */
function copyToClipboard(text: string): void {
  try {
    void navigator.clipboard?.writeText(text).catch(() => {});
  } catch {
    // jsdom / permission-denied — the visible <code> value remains selectable.
  }
}

function ChallengeField({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-sm font-medium text-foreground">{label}</span>
      <div className="flex items-start gap-2">
        <code className="block min-w-0 flex-1 break-all rounded-md border border-border bg-muted px-3 py-2 font-mono text-sm tabular-nums text-foreground">
          {value}
        </code>
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-label={`Copy ${label.toLowerCase()}`}
          onClick={() => copyToClipboard(value)}
        >
          <Copy className="size-3.5" aria-hidden="true" />
          Copy
        </Button>
      </div>
    </div>
  );
}

export function DomainClaimsSettings() {
  const queryClient = useQueryClient();

  const { data, isLoading, isError, error } = useQuery<DomainClaimListResponse>({
    queryKey: CLAIMS_KEY,
    queryFn: () => bffGet<DomainClaimListResponse>(CLAIMS_PATH),
    retry: false,
  });

  const [domainInput, setDomainInput] = useState("");
  // The freshly-minted challenge from the CREATE response — the one place the
  // record is certain to exist right after POST (no per-claim GET endpoint).
  const [challenge, setChallenge] = useState<DomainClaimCreateResponse | null>(null);
  // ONE ephemeral action alert at a time (create error / verify 422 / verify 503 /
  // revoke error) — a failed verify is an alert, never a seal state (R2).
  const [actionAlert, setActionAlert] = useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<DomainClaimListItem | null>(null);

  const createClaim = useMutation({
    mutationFn: (domain: string) =>
      bffPost<DomainClaimCreateResponse>(CLAIMS_PATH, { domain }),
    onSuccess: (resp) => {
      setActionAlert(null);
      setChallenge(resp);
      setDomainInput("");
      void queryClient.invalidateQueries({ queryKey: CLAIMS_KEY });
    },
    onError: (err) => setActionAlert(getErrorTitle(err)),
  });

  const verifyClaim = useMutation({
    mutationFn: (claim: DomainClaimListItem) =>
      bffPost<DomainClaimVerifyResponse>(`${CLAIMS_PATH}/${claim.claim_id}/verify`, {}),
    onSuccess: (resp) => {
      setActionAlert(null);
      // The seal flips ONLY from the verify SUCCESS response (R2) — update the
      // cached row in place; no optimistic write ever precedes the response.
      queryClient.setQueryData<DomainClaimListResponse>(CLAIMS_KEY, (prev) =>
        prev
          ? {
              claims: prev.claims.map((c) =>
                c.claim_id === resp.claim_id
                  ? { ...c, status: resp.status, verified_at: resp.verified_at }
                  : c,
              ),
            }
          : prev,
      );
    },
    onError: (err) => {
      // Two DISTINCT failure UX paths — never collapsed (§0 Issue #3):
      // 422 = the record is absent/mismatched (fix it, then retry);
      // 503 = the DNS lookup itself failed (transient — just retry).
      const code = bffCode(err);
      if (code === "ERR_DOMAIN_VERIFICATION_FAILED") {
        setActionAlert(
          "DNS record absent or does not match — fix the TXT record, then retry.",
        );
      } else if (code === "ERR_DNS_LOOKUP_FAILED") {
        setActionAlert("DNS lookup failed — try again.");
      } else {
        setActionAlert(getErrorTitle(err));
      }
    },
  });

  const revokeClaim = useMutation({
    mutationFn: (claimId: string) => bffDelete(`${CLAIMS_PATH}/${claimId}`),
    onSuccess: () => {
      setActionAlert(null);
      setRevokeTarget(null);
      // Hard delete server-side (no tombstone) — the row disappears on refetch (M5).
      void queryClient.invalidateQueries({ queryKey: CLAIMS_KEY });
    },
    onError: (err) => {
      setRevokeTarget(null);
      setActionAlert(getErrorTitle(err));
    },
  });

  if (isLoading) {
    return <Loading label="Loading domain claims" />;
  }

  // Owner-only surface: 403 ERR_AUTH_FORBIDDEN (and any other load failure) renders
  // ErrorState — no claim data, no mutating control (R1). The SERVER gate is the defense.
  if (isError) {
    return <ErrorState title={getErrorTitle(error)} />;
  }

  const claims = data?.claims ?? [];

  return (
    <div className="flex flex-col gap-6" data-slot="domain-claims-settings">
      <p className="text-sm text-muted-foreground">
        Prove your tenant owns an email domain by publishing a DNS TXT record. Verified
        domains route matching signups and SSO logins into this workspace.
      </p>

      <form
        className="flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const domain = domainInput.trim();
          if (!domain || createClaim.isPending) return;
          setActionAlert(null);
          createClaim.mutate(domain);
        }}
      >
        <div className="flex flex-1 flex-col gap-1.5">
          <label htmlFor="domain-claim-input" className="text-sm font-medium text-foreground">
            Domain
          </label>
          <Input
            id="domain-claim-input"
            value={domainInput}
            onChange={(e) => setDomainInput(e.target.value)}
            placeholder="acme.com"
            autoComplete="off"
          />
        </div>
        <Button type="submit" disabled={createClaim.isPending}>
          {createClaim.isPending ? "Adding…" : "Add domain"}
        </Button>
      </form>

      {actionAlert && (
        <p role="alert" aria-live="polite" className="text-sm text-destructive-text">
          {actionAlert}
        </p>
      )}

      {challenge && (
        <div
          data-slot="dns-challenge"
          className="flex flex-col gap-3 rounded-lg border border-border bg-muted/30 p-4"
        >
          <p className="text-sm text-muted-foreground">
            Publish this DNS record for <span className="font-medium text-foreground">{challenge.domain}</span>,
            then click Verify now. Propagation can take a few minutes.
          </p>
          <ChallengeField label="Record type" value={challenge.dns_record_type} />
          <ChallengeField label="Record name" value={challenge.dns_record_name} />
          <ChallengeField label="Record value" value={challenge.dns_record_value} />
        </div>
      )}

      {claims.length === 0 ? (
        <Empty
          title="No domain claims yet"
          description="Add a domain above to start DNS-TXT verification."
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Domain</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Expires</TableHead>
              <TableHead>
                <span className="sr-only">Actions</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {claims.map((claim) => (
              <TableRow key={claim.claim_id}>
                <TableCell className="font-mono text-sm tabular-nums">{claim.domain}</TableCell>
                <TableCell>
                  <DomainStatusSeal claim={claim} />
                </TableCell>
                <TableCell className="font-mono text-sm tabular-nums text-muted-foreground">
                  {claim.status === "verified"
                    ? "—"
                    : new Date(claim.expires_at).toLocaleString()}
                </TableCell>
                <TableCell>
                  <div className="flex justify-end gap-2">
                    {claim.status === "pending" && sealState(claim) === "pending" && (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={verifyClaim.isPending}
                        onClick={() => {
                          setActionAlert(null);
                          verifyClaim.mutate(claim);
                        }}
                      >
                        Verify now
                      </Button>
                    )}
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={revokeClaim.isPending}
                      onClick={() => setRevokeTarget(claim)}
                    >
                      Revoke
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <Dialog
        open={revokeTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRevokeTarget(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Revoke domain claim?</DialogTitle>
            <DialogDescription>
              {revokeTarget
                ? `Revoking ${revokeTarget.domain} permanently deletes the claim. New signups on this domain will no longer join this workspace; existing members are unaffected.`
                : ""}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setRevokeTarget(null)}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={revokeClaim.isPending}
              onClick={() => {
                if (revokeTarget) revokeClaim.mutate(revokeTarget.claim_id);
              }}
            >
              Revoke
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
