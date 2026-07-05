/**
 * lib/impersonation-cookie.ts
 *
 * The M1 cookie-envelope encode/decode logic (impersonation-ui TASK.md §3 CONTRACT
 * Part A/M1, and the ⚠ flag surfaced at freeze) — concentrated ONCE here and shared
 * by all 3 call sites that touch the `ai_proxy_impersonation_session` cookie:
 *   - Mint (`app/api/platform/impersonation/route.ts`, POST)  — WRITES it
 *   - Status (`app/api/platform/impersonation/route.ts`, GET) — READS it
 *   - the gw proxy (`app/api/gw/[...path]/route.ts`)          — READS it
 *
 * Per the task's own freeze flag, this is the single highest-leverage code in the
 * whole task: a bug here breaks impersonation everywhere at once. Every function
 * here is engineered to degrade CLOSED — a missing/malformed cookie decodes to
 * `null` ("treat as absent"), NEVER throws — so a corrupted cookie can only ever
 * stop impersonation from working, never leak a token into the wrong request.
 *
 * The cookie's VALUE is `encodeURIComponent(JSON.stringify({token, session_id,
 * expires_at}))` — a small envelope, never a bare JWT (M1) — because the gateway's
 * `GET /admin/auth/me` carries neither `session_id` nor `expires_at`, so a
 * bare-token cookie could not recover them across a page refresh with zero gateway
 * change. A raw, un-encoded JSON string is also not a valid cookie-octet (RFC 6265
 * excludes `,`/`"`/`;`), so the URI-encode round-trip is load-bearing, not cosmetic.
 */

export const IMPERSONATION_COOKIE_NAME = "ai_proxy_impersonation_session";

export interface ImpersonationEnvelope {
  token: string;
  session_id: string;
  expires_at: number;
}

function isImpersonationEnvelope(value: unknown): value is ImpersonationEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.token === "string" &&
    typeof v.session_id === "string" &&
    typeof v.expires_at === "number"
  );
}

/** Extract ONE named cookie's raw (still URI-encoded) value out of a Cookie header. */
function readCookieValue(cookieHeader: string, name: string): string | null {
  const match = cookieHeader.match(new RegExp(`${name}=([^;]+)`));
  return match?.[1] ?? null;
}

/** Build the RAW (URI-encoded) envelope value for the cookie. Never a bare JWT (M1). */
export function encodeImpersonationEnvelope(envelope: ImpersonationEnvelope): string {
  return encodeURIComponent(JSON.stringify(envelope));
}

/**
 * Decode a raw (already-extracted) impersonation-cookie value. ANY failure — not
 * URI-decodable, not valid JSON, missing/wrong-typed field — degrades to `null`,
 * NEVER throws (M1's own fail-closed requirement).
 */
export function decodeImpersonationEnvelope(raw: string): ImpersonationEnvelope | null {
  try {
    const parsed: unknown = JSON.parse(decodeURIComponent(raw));
    return isImpersonationEnvelope(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/**
 * Read the impersonation cookie's RAW (still-encoded) value out of a request's
 * Cookie header, or `null` if the cookie is entirely absent. Distinct from
 * `readImpersonationEnvelope` below: callers that must distinguish "cookie absent"
 * from "cookie present but malformed" (Status's self-heal path) use this first.
 */
export function readRawImpersonationCookie(cookieHeader: string): string | null {
  return readCookieValue(cookieHeader, IMPERSONATION_COOKIE_NAME);
}

/**
 * Read + decode the impersonation cookie out of a request's Cookie header in one
 * step. Absent OR malformed both collapse to `null` — the convenience form for
 * call sites (the gw proxy) that treat "absent" and "malformed" identically.
 */
export function readImpersonationEnvelope(cookieHeader: string): ImpersonationEnvelope | null {
  const raw = readRawImpersonationCookie(cookieHeader);
  if (!raw) return null;
  return decodeImpersonationEnvelope(raw);
}

function secureAttr(): string {
  return process.env.NODE_ENV !== "development" ? "; Secure" : "";
}

/** Set-Cookie value that WRITES a fresh envelope (Mint's own 201 success path). */
export function buildImpersonationCookie(envelope: ImpersonationEnvelope, maxAgeSeconds: number): string {
  const value = encodeImpersonationEnvelope(envelope);
  return `${IMPERSONATION_COOKIE_NAME}=${value}; HttpOnly${secureAttr()}; SameSite=Strict; Path=/; Max-Age=${maxAgeSeconds}`;
}

/**
 * Set-Cookie value that CLEARS the impersonation cookie — End's 204 path, Status's
 * self-heal path, and the gw proxy's source-aware control-plane-401 clear all share
 * this one builder (mirrors the existing `buildClearCookieValue`'s exact pattern
 * for `ai_proxy_session`).
 */
export function buildClearImpersonationCookie(): string {
  return `${IMPERSONATION_COOKIE_NAME}=; HttpOnly${secureAttr()}; SameSite=Strict; Path=/; Max-Age=0`;
}
