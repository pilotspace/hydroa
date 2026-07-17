/**
 * lib/bff-validation.ts — v50 bff-input-validation
 *
 * Shared, fail-closed input validation for the BFF route boundary. The gateway
 * is the auth AUTHORITY (email format, password policy, uniqueness); this layer
 * is a THIN presence + type + bounds gate that runs BEFORE any upstream fetch so
 * no malformed/oversized body ever reaches the gateway.
 *
 * On any failure (invalid JSON, non-object body, schema miss) parseJsonBody
 * returns a ready 400 { code: "ERR_BFF_PAYLOAD_INVALID" } response — preserving
 * the shipped contract (see tests-bff/route-handlers.test.ts) — and never throws
 * past its boundary.
 */

import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";

// Guard bounds (not policy): generous caps that only stop abusive payloads.
// email ≤320 (RFC 5321 max), password ≤4096, tenant_name ≤200.
export const loginSchema = z.object({
  email: z.string().min(1).max(320),
  password: z.string().min(1).max(4096),
});

// activation-quickstart: account_type is OPTIONAL and additive — every existing
// caller that omits the field stays byte-identical (the gateway's own "business"
// default applies). A present-but-invalid value (guard-bound, not a policy call —
// the enum IS the policy here) 400s here before any gateway round trip.
export const signupSchema = z.object({
  tenant_name: z.string().min(1).max(200),
  email: z.string().min(1).max(320),
  password: z.string().min(1).max(4096),
  account_type: z.enum(["personal", "business"]).optional(),
});

// member-invite-ui: the accept-invite BFF route body. Same guard-bound shape as
// loginSchema's password field — presence + bounds only, the gateway is the
// password-policy authority (and separately validates the invite token itself).
export const acceptInviteSchema = z.object({
  password: z.string().min(1).max(4096),
});

export type LoginBody = z.infer<typeof loginSchema>;
export type SignupBody = z.infer<typeof signupSchema>;
export type AcceptInviteBody = z.infer<typeof acceptInviteSchema>;

export type ParseResult<T> =
  | { ok: true; data: T }
  | { ok: false; response: NextResponse };

function payloadInvalid(): NextResponse {
  return NextResponse.json({ code: "ERR_BFF_PAYLOAD_INVALID" }, { status: 400 });
}

/**
 * Parse + validate a JSON request body against a schema, fail-closed.
 * Never throws: a malformed/non-object/invalid body yields a 400 response.
 */
export async function parseJsonBody<T>(
  req: NextRequest,
  schema: z.ZodSchema<T>,
): Promise<ParseResult<T>> {
  let raw: unknown;
  try {
    raw = await req.json();
  } catch {
    return { ok: false, response: payloadInvalid() };
  }

  // Reject non-object bodies (string / number / array / null) before zod so the
  // schema only ever sees a record — defense-in-depth against odd payloads.
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    return { ok: false, response: payloadInvalid() };
  }

  const parsed = schema.safeParse(raw);
  if (!parsed.success) {
    return { ok: false, response: payloadInvalid() };
  }
  return { ok: true, data: parsed.data };
}

// A login domain hint is the only caller-supplied value the oidc relay forwards.
// Bound it (≤253, the DNS name limit) and reject control/whitespace chars; the
// relay separately encodeURIComponent()s the value, so a hostile string can
// never smuggle extra query params — it stays a single encoded `domain` param
// (see tests-bff/oidc-login-relay.test.tsx). A rejected value is dropped
// (treated as absent) so the relay stays fail-safe.
const DOMAIN_MAX = 253;

// Reject anything outside printable ASCII (0x21–0x7E): C0 controls + space
// (≤0x20), DEL + C1 controls + all non-ASCII (≥0x7F). This honors the "no
// control chars" contract fully (C1 controls like U+0085 are control chars) and
// is the right shape for a DNS domain hint (IDNs arrive as ASCII punycode).
// A char-code scan avoids a control-char literal in a regex while staying explicit.
function hasForbiddenChar(s: string): boolean {
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c <= 0x20 || c >= 0x7f) return true;
  }
  return false;
}

export function sanitizeDomain(raw: string | null): string | null {
  if (raw === null) return null;
  if (raw.length === 0 || raw.length > DOMAIN_MAX) return null;
  return hasForbiddenChar(raw) ? null : raw;
}
