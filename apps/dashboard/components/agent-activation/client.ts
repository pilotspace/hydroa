/**
 * components/agent-activation/client.ts — BFF calls + input normalization for the
 * /activate device-approval surface (device-activate-page §3).
 *
 * The plaintext user_code travels ONLY in a POST body (bffPost attaches the session
 * cookie server-side, never a URL). Redirect-safety helpers (sanitizeNext / loginNextTarget /
 * buildLoginBounceUrl) live in @/lib/bff-client beside the /login bounce they guard.
 */

import { bffPost } from "@/lib/bff-client";

/**
 * Normalize a loosely-typed user_code to canonical XXXX-XXXX (mirrors the gateway's
 * `_normalize_user_code`): collapse whitespace, uppercase, drop dashes, re-insert the
 * dash at position 4 when exactly 8 chars. A non-8-char value is left uppercased +
 * dash-free (it hashes to no match -> a clean not-previewable outcome).
 */
export function normalizeUserCode(raw: string): string {
  let s = raw.split(/\s+/).join("").toUpperCase().replace(/-/g, "");
  if (s.length === 8) s = `${s.slice(0, 4)}-${s.slice(4)}`;
  return s;
}

export interface PreviewFacts {
  scope: string;
  status: "pending";
  expires_in: number;
  interval: number;
  default_budget_usd: string;
}

export interface ApprovalResult {
  status: string;
}

export function previewDeviceGrant(userCode: string): Promise<PreviewFacts> {
  return bffPost<PreviewFacts>("/oauth/device/preview", { user_code: userCode });
}

export function approveDeviceGrant(userCode: string): Promise<ApprovalResult> {
  return bffPost<ApprovalResult>("/oauth/device/approve", { user_code: userCode });
}

export function denyDeviceGrant(userCode: string): Promise<ApprovalResult> {
  return bffPost<ApprovalResult>("/oauth/device/deny", { user_code: userCode });
}
