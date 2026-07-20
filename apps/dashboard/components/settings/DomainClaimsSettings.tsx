"use client";

/**
 * DomainClaimsSettings — the "Domains" tab of /settings (OWNER-only console;
 * domain-claims-console TASK.md §3 CONTRACT — FROZEN @ v1, softened by
 * dns-verify-softeners TASK.md §3 — FROZEN @ v2).
 *
 * Pure pass-through over the FROZEN gateway /admin/domain-claims API via the
 * authenticated BFF proxy (bffGet/bffPost/bffDelete -> /api/gw/*): list (M1),
 * create -> inline DNS-TXT challenge with per-field copy (M3), verify-now with
 * distinct 400-mismatch vs 503-lookup alerts (M4), revoke behind a confirm (M5).
 *
 * dns-verify-softeners (v2) adds a purely ADDITIVE softening LAYER — the verify
 * endpoint, its error taxonomy, the seal-flips-only-on-200 rule (R2), and
 * AUTO-JOIN are ALL unchanged:
 *   - a bounded client AUTO-POLL (useVerifyPoll) that re-checks each pending
 *     claim and flips its seal on a 200 with NO click, reframes a 400/503 as a
 *     CALM "still checking" (never a red alarm), and reserves the loud alert for
 *     TERMINAL 410/409/404;
 *   - challenge-card affordances: one-block "Copy record" (M6), a "Not the DNS
 *     owner?" hand-off (M7), a registrar deep-link / static-list display (M8), a
 *     non-blocking "Verify later" (M9), and an "Email me when it's live" opt-in
 *     over the domain-verify-notify backend (M10).
 * The MANUAL "Verify now" button and its LOUD role="alert" on a manual 400/503
 * are KEPT VERBATIM (v2 narrowing, Tin 2026-07-20) — the frozen
 * domain-claims-console suite owns them; only the auto-poll path is softened.
 * All authorization + DNS verification stays SERVER-side (R4).
 */

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Bell, Copy, Mail } from "lucide-react";
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
import {
  useVerifyPoll,
  DEFAULT_VERIFY_POLL,
  type VerifyOutcome,
} from "./useVerifyPoll";
import { MemberVerifyCodeEntry, type MemberVerifyClaim } from "./MemberVerifyCodeEntry";

export interface DomainClaimListItem {
  claim_id: string;
  domain: string;
  status: "pending" | "verified";
  dns_record_name: string;
  dns_record_value: string;
  expires_at: string;
  verified_at: string | null;
  // ADDITIVE (domain-verify-notify §3, FROZEN): the "email me when it's live" state.
  notify_requested_at: string | null;
  notified_at: string | null;
  // ADDITIVE (member-verified-code-entry §3, FROZEN): the rung-1 member trust marker;
  // consumed VERBATIM from the frozen member-verified-recognition backend (6a75579).
  member_verified_at?: string | null;
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

/** registrar-hint §3 (FROZEN): the NS-inferred deep-link hint, DISPLAY-only here. */
interface RegistrarHintResponse {
  domain: string;
  registrar: string | null;
  deep_link_url: string | null;
  fallback: boolean;
}

const CLAIMS_KEY = ["admin-domain-claims"] as const;
const CLAIMS_PATH = "/admin/domain-claims";

/** Static common-registrar landing pages — the graceful fallback when the
 * NS-inferred deep link is unavailable (registrar-hint returns fallback:true or
 * the lookup fails). Display-only, safe new-tab external links (M8). */
const COMMON_REGISTRARS: ReadonlyArray<{ name: string; url: string }> = [
  { name: "Cloudflare", url: "https://dash.cloudflare.com/login" },
  { name: "GoDaddy", url: "https://dcc.godaddy.com/manage/dns" },
  { name: "Namecheap", url: "https://ap.www.namecheap.com/domains/list" },
  { name: "Google Domains", url: "https://domains.google.com/registrar" },
];

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

/** The one-block copy payload (M6) — type + name + value as a single pasteable block. */
function recordBlock(c: DomainClaimCreateResponse): string {
  return `Type: ${c.dns_record_type}\nName: ${c.dns_record_name}\nValue: ${c.dns_record_value}`;
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

/** Defence-in-depth: only ever put an http(s) URL in an href. The registrar-hint
 * backend already returns curated static https literals (SSRF-closed), but React
 * does NOT block a `javascript:`/`data:` href — so a scheme guard here fails closed
 * to the static provider list rather than trusting the response blindly. */
function isSafeHttpUrl(url: string): boolean {
  return /^https?:\/\//i.test(url);
}

/** Registrar deep-link display (M8) — DISPLAY-only; no inference happens here. */
function RegistrarLinks({ hint }: { hint: RegistrarHintResponse | undefined }) {
  const deep =
    hint && !hint.fallback && hint.deep_link_url && hint.registrar && isSafeHttpUrl(hint.deep_link_url)
      ? { url: hint.deep_link_url, registrar: hint.registrar }
      : null;

  return (
    <div data-slot="registrar-links" className="flex flex-col gap-1.5">
      {deep ? (
        <a
          href={deep.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm font-medium text-accent-soft-foreground underline underline-offset-2"
        >
          Open {deep.registrar} DNS settings ↗
        </a>
      ) : (
        <>
          <span className="text-sm font-medium text-foreground">Open your DNS provider</span>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {COMMON_REGISTRARS.map((r) => (
              <a
                key={r.name}
                href={r.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm text-accent-soft-foreground underline underline-offset-2"
              >
                {r.name} ↗
              </a>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/** Not-the-DNS-owner hand-off (M7) — a shareable mailto (clipboard fallback);
 * changes NOTHING about the claim's verification state. */
function DnsHandoff({ challenge }: { challenge: DomainClaimCreateResponse }) {
  const subject = `Please add a DNS TXT record for ${challenge.domain}`;
  const body = [
    `Hi — please add this DNS TXT record so we can verify ownership of ${challenge.domain}:`,
    "",
    `Type:  ${challenge.dns_record_type}`,
    `Name:  ${challenge.dns_record_name}`,
    `Value: ${challenge.dns_record_value}`,
    "",
    "It can take a few minutes to propagate after you save it. Thank you!",
  ].join("\n");
  const mailto = `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;

  return (
    <div data-slot="dns-handoff" className="flex flex-wrap items-center gap-2">
      <span className="text-sm text-muted-foreground">Not the DNS owner?</span>
      <a
        href={mailto}
        className="inline-flex items-center gap-1 text-sm font-medium text-accent-soft-foreground underline underline-offset-2"
      >
        <Mail className="size-3.5" aria-hidden="true" />
        Email your DNS admin
      </a>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => copyToClipboard(`${subject}\n\n${body}`)}
      >
        <Copy className="size-3.5" aria-hidden="true" />
        Copy message
      </Button>
    </div>
  );
}

export interface DomainClaimsSettingsProps {
  /** Auto-poll timing seam (test injection) — defaults to the frozen ≈30s / ≈15min. */
  poll?: { intervalMs?: number; ceilingMs?: number };
}

export function DomainClaimsSettings({ poll }: DomainClaimsSettingsProps = {}) {
  const queryClient = useQueryClient();

  const { data, isLoading, isError, error } = useQuery<DomainClaimListResponse>({
    queryKey: CLAIMS_KEY,
    queryFn: () => bffGet<DomainClaimListResponse>(CLAIMS_PATH),
    retry: false,
  });

  const [domainInput, setDomainInput] = useState("");
  const [challenge, setChallenge] = useState<DomainClaimCreateResponse | null>(null);
  const [actionAlert, setActionAlert] = useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<DomainClaimListItem | null>(null);
  // The auto-poll's aria-live announcement when a claim flips verified with no click.
  const [verifiedAnnounce, setVerifiedAnnounce] = useState<string | null>(null);
  // The challenge card's "email me when it's live" opt-in reflection (M10).
  const [notifyOptedIn, setNotifyOptedIn] = useState(false);

  const claims = data?.claims ?? [];

  // ── registrar-hint (M8): DISPLAY the NS-inferred deep-link for the fresh claim ──
  const { data: registrarHint } = useQuery<RegistrarHintResponse>({
    queryKey: ["registrar-hint", challenge?.domain],
    queryFn: () =>
      bffGet<RegistrarHintResponse>(
        `${CLAIMS_PATH}/registrar-hint?domain=${encodeURIComponent(challenge!.domain)}`,
      ),
    enabled: challenge !== null,
    retry: false,
  });

  /** The single seal-flip site (frozen R2) — shared by the manual mutation AND the
   * auto-poll, so the seal only ever transitions from a 200 verify response. */
  const applyVerified = (resp: DomainClaimVerifyResponse) => {
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
  };

  /** The member-verify single seal-flip site (member-verified-code-entry §3 item 6,
   * FROZEN) — parallels applyVerified: patches ONLY member_verified_at from the 200
   * body into the cached claim; `status` is left untouched (stays "pending", M9). */
  const applyMemberVerified = (item: MemberVerifyClaim) => {
    queryClient.setQueryData<DomainClaimListResponse>(CLAIMS_KEY, (prev) =>
      prev
        ? {
            claims: prev.claims.map((c) =>
              c.claim_id === item.claim_id
                ? { ...c, member_verified_at: item.member_verified_at }
                : c,
            ),
          }
        : prev,
    );
  };

  const createClaim = useMutation({
    mutationFn: (domain: string) => bffPost<DomainClaimCreateResponse>(CLAIMS_PATH, { domain }),
    onSuccess: (resp) => {
      setActionAlert(null);
      setNotifyOptedIn(false);
      setChallenge(resp);
      setDomainInput("");
      void queryClient.invalidateQueries({ queryKey: CLAIMS_KEY });
    },
    onError: (err) => setActionAlert(getErrorTitle(err)),
  });

  // MANUAL verify (UNCHANGED, v2) — keeps its loud, distinct 400 vs 503 alerts.
  const verifyClaim = useMutation({
    mutationFn: (claim: DomainClaimListItem) =>
      bffPost<DomainClaimVerifyResponse>(`${CLAIMS_PATH}/${claim.claim_id}/verify`, {}),
    onSuccess: (resp) => {
      setActionAlert(null);
      applyVerified(resp);
    },
    onError: (err) => {
      // Two DISTINCT manual-failure UX paths — never collapsed (owned by the frozen
      // domain-claims-console suite): 400 = record absent/mismatched; 503 = lookup failed.
      const code = bffCode(err);
      if (code === "ERR_DOMAIN_VERIFICATION_FAILED") {
        setActionAlert("DNS record absent or does not match — fix the TXT record, then retry.");
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
      void queryClient.invalidateQueries({ queryKey: CLAIMS_KEY });
    },
    onError: (err) => {
      setRevokeTarget(null);
      setActionAlert(getErrorTitle(err));
    },
  });

  // ── notify opt-in / opt-out (M10) — body carries NO email (server-derived) ──────
  const notifyOptin = useMutation({
    mutationFn: (claimId: string) =>
      bffPost<DomainClaimListItem>(`${CLAIMS_PATH}/${claimId}/notify`, {}),
    onSuccess: () => {
      setNotifyOptedIn(true);
      void queryClient.invalidateQueries({ queryKey: CLAIMS_KEY });
    },
    onError: (err) => setActionAlert(getErrorTitle(err)),
  });
  const notifyOptout = useMutation({
    mutationFn: (claimId: string) => bffDelete(`${CLAIMS_PATH}/${claimId}/notify`),
    onSuccess: () => {
      setNotifyOptedIn(false);
      void queryClient.invalidateQueries({ queryKey: CLAIMS_KEY });
    },
    onError: (err) => setActionAlert(getErrorTitle(err)),
  });

  // ── AUTO-POLL (M1–M5) — bounded, tab-aware, additive; seal flips via applyVerified ──
  const pollableIds = claims
    .filter((c) => c.status === "pending" && sealState(c) === "pending")
    .map((c) => c.claim_id);

  const runVerify = async (id: string): Promise<VerifyOutcome<DomainClaimVerifyResponse>> => {
    try {
      const resp = await bffPost<DomainClaimVerifyResponse>(`${CLAIMS_PATH}/${id}/verify`, {});
      return { type: "verified", value: resp };
    } catch (err) {
      const code = bffCode(err);
      const status = err instanceof BffError ? err.status : 0;
      // Not live yet / transient resolver error → CALM, keep polling (R-a).
      if (code === "ERR_DOMAIN_VERIFICATION_FAILED" || code === "ERR_DNS_LOOKUP_FAILED") {
        return { type: "calm" };
      }
      if (code === "ERR_DOMAIN_CLAIM_EXPIRED") {
        return { type: "terminal", message: "This verification challenge expired — request a new one." };
      }
      if (code === "ERR_DOMAIN_ALREADY_VERIFIED") {
        return { type: "terminal", message: "This domain is already verified by another tenant." };
      }
      if (code === "ERR_DOMAIN_CLAIM_NOT_FOUND") {
        return { type: "terminal", message: "This domain claim no longer exists — refresh the list." };
      }
      if (status === 429) return { type: "backoff" };
      return { type: "error" }; // treat as calm — self-heals next tick
    }
  };

  const pollStatus = useVerifyPoll<DomainClaimVerifyResponse>({
    pollableIds,
    config: {
      intervalMs: poll?.intervalMs ?? DEFAULT_VERIFY_POLL.intervalMs,
      ceilingMs: poll?.ceilingMs ?? DEFAULT_VERIFY_POLL.ceilingMs,
    },
    verify: runVerify,
    onVerified: (resp) => {
      applyVerified(resp);
      setVerifiedAnnounce(`${resp.domain} is verified`);
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

  return (
    <div className="flex flex-col gap-6" data-slot="domain-claims-settings">
      {verifiedAnnounce && (
        <p role="status" aria-live="polite" className="sr-only">
          {verifiedAnnounce}
        </p>
      )}

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

      {challenge && (() => {
        const activeClaim = claims.find((c) => c.claim_id === challenge.claim_id);
        // M4/§3 item 8: EXPANDED while not-yet-verified/member-verified (or the
        // claim hasn't landed in the list yet — fail-open to the action-needed
        // display, never a falsely-reassuring collapse); COLLAPSED once
        // member_verified_at is set OR the claim is Owner-verified.
        const showCodeEntry =
          !activeClaim || (activeClaim.member_verified_at == null && activeClaim.status !== "verified");
        return (
          <>
            {showCodeEntry && (
              <MemberVerifyCodeEntry claimId={challenge.claim_id} onVerified={applyMemberVerified} />
            )}
            <div
              data-slot="dns-challenge"
              className="flex flex-col gap-3 rounded-lg border border-border bg-muted/30 p-4"
            >
          <p className="text-sm text-muted-foreground">
            Publish this DNS record for{" "}
            <span className="font-medium text-foreground">{challenge.domain}</span>. We&apos;ll check
            automatically and verify it as soon as it&apos;s live — propagation can take a few minutes.
          </p>
          <ChallengeField label="Record type" value={challenge.dns_record_type} />
          <ChallengeField label="Record name" value={challenge.dns_record_name} />
          <ChallengeField label="Record value" value={challenge.dns_record_value} />

          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => copyToClipboard(recordBlock(challenge))}
            >
              <Copy className="size-3.5" aria-hidden="true" />
              Copy record
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => setChallenge(null)}>
              Verify later
            </Button>
          </div>

          <RegistrarLinks hint={registrarHint} />
          <DnsHandoff challenge={challenge} />

          <div data-slot="notify-optin" className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-pressed={notifyOptedIn}
              disabled={notifyOptin.isPending || notifyOptout.isPending}
              onClick={() =>
                notifyOptedIn
                  ? notifyOptout.mutate(challenge.claim_id)
                  : notifyOptin.mutate(challenge.claim_id)
              }
            >
              <Bell className="size-3.5" aria-hidden="true" />
              Email me when it&apos;s live
            </Button>
            {notifyOptedIn && (
              <span className="text-sm text-muted-foreground">
                We&apos;ll email you when it&apos;s live — you can close this tab.
              </span>
            )}
          </div>
            </div>
          </>
        );
      })()}

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
            {claims.map((claim) => {
              const rowPoll = pollStatus[claim.claim_id];
              return (
                <TableRow key={claim.claim_id}>
                  <TableCell className="font-mono text-sm tabular-nums">{claim.domain}</TableCell>
                  <TableCell>
                    <div className="flex flex-col gap-1">
                      <DomainStatusSeal claim={claim} />
                      {rowPoll?.kind === "checking" && (
                        <p className="text-xs text-muted-foreground">
                          Waiting for DNS to propagate — checking automatically…
                        </p>
                      )}
                      {rowPoll?.kind === "terminal" && (
                        <p role="alert" className="text-xs text-destructive-text">
                          {rowPoll.message}
                        </p>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="font-mono text-sm tabular-nums text-muted-foreground">
                    {claim.status === "verified" ? "—" : new Date(claim.expires_at).toLocaleString()}
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
              );
            })}
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
