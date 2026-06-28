/**
 * lib/playground-token.ts — SERVER-ONLY (imported only by the BFF gateway proxy route).
 *
 * Server-side token exchange. The browser holds only a session JWT, but the /v1
 * data plane requires an sk-/agent key (edge ext_authz). For a /v1 request the BFF
 * mints a SHORT-LIVED, spend-capped playground sk- key (POST
 * /admin/keys/playground-token, JWT-authed), caches it IN SERVER MEMORY keyed by
 * the session, reuses it until just before it expires, and attaches it upstream.
 *
 * Security: the plaintext key never leaves the server — it is never returned to the
 * browser and never logged. The cache key is the JWT's opaque signature segment, so
 * the full token (with its claims) is not held as a map key.
 */

// Build-time guard: throws if this module is ever pulled into a Client Component
// bundle (security review F2). Keeps the mint logic + the resolved key server-only.
import "server-only";

interface MintedToken {
  key: string;
  expiresAtMs: number;
}

const SAFETY_MARGIN_MS = 60_000; // re-mint this long before the real expiry
// Used only if expires_at is missing/unparseable. Deliberately BELOW the gateway's
// default 30-min (1800 s) playground TTL so an absent expiry errs toward an early
// re-mint (never serving a key past its real expiry) — a conservative floor, not a match.
const FALLBACK_TTL_MS = 25 * 60_000;
const MAX_ENTRIES = 500; // coarse bound on the in-memory cache

// cacheKey -> a resolved token OR an in-flight mint promise (dedupes concurrent mints).
const cache = new Map<string, MintedToken | Promise<MintedToken | null>>();

/**
 * The cache key is the JWT's opaque signature segment — unique per user/issuance and
 * claim-free. Returns null for a non-3-segment (malformed) token so we NEVER hold a
 * claim-bearing raw token as a map key (security review N2); such a token would fail
 * auth at the gateway anyway, so the caller mints once without caching.
 */
function cacheKeyFor(jwt: string): string | null {
  const parts = jwt.split(".");
  return parts.length === 3 && parts[2] ? parts[2] : null;
}

function evictIfNeeded(): void {
  if (cache.size <= MAX_ENTRIES) return;
  const overflow = cache.size - MAX_ENTRIES;
  let i = 0;
  for (const k of cache.keys()) {
    if (i++ >= overflow) break;
    cache.delete(k);
  }
}

async function mint(jwt: string, gatewayUrl: string): Promise<MintedToken | null> {
  let res: Response;
  try {
    res = await fetch(`${gatewayUrl}/admin/keys/playground-token`, {
      method: "POST",
      headers: { Authorization: `Bearer ${jwt}`, "Content-Type": "application/json" },
      body: "{}",
    });
  } catch {
    return null; // network failure — honest degrade (caller falls back to the JWT)
  }
  if (!res.ok) return null;
  let body: { key?: string; expires_at?: string | null };
  try {
    body = (await res.json()) as { key?: string; expires_at?: string | null };
  } catch {
    return null;
  }
  if (!body.key) return null;
  const parsed = body.expires_at ? Date.parse(body.expires_at) : NaN;
  const expiresAtMs = Number.isFinite(parsed) ? parsed : Date.now() + FALLBACK_TTL_MS;
  return { key: body.key, expiresAtMs };
}

/**
 * Return a valid playground sk- key for `jwt`, minting (and caching) one if needed.
 * Returns null if minting fails — the caller then honestly degrades (forwards the
 * JWT, which the data plane rejects inline, never logging the user out).
 */
export async function getPlaygroundToken(jwt: string, gatewayUrl: string): Promise<string | null> {
  const key = cacheKeyFor(jwt);
  // Malformed token (no opaque signature segment): mint once WITHOUT caching — we never
  // hold a claim-bearing raw token as a cache key (N2). The gateway rejects it anyway.
  if (key === null) {
    const tok = await mint(jwt, gatewayUrl);
    return tok?.key ?? null;
  }
  const now = Date.now();
  const existing = cache.get(key);
  if (existing) {
    if (existing instanceof Promise) {
      const r = await existing;
      return r?.key ?? null;
    }
    if (existing.expiresAtMs - SAFETY_MARGIN_MS > now) return existing.key;
    cache.delete(key); // stale — fall through to re-mint
  }
  const pending = mint(jwt, gatewayUrl).then(
    (tok) => {
      if (tok) {
        cache.set(key, tok);
        evictIfNeeded();
      } else {
        cache.delete(key);
      }
      return tok;
    },
    () => {
      cache.delete(key);
      return null;
    },
  );
  cache.set(key, pending);
  const tok = await pending;
  return tok?.key ?? null;
}

/** Drop the cached token for a session — e.g. after a data-plane 401, force a re-mint. */
export function invalidatePlaygroundToken(jwt: string): void {
  const key = cacheKeyFor(jwt);
  if (key !== null) cache.delete(key);
}

/** Test-only: clear the in-memory cache between cases. */
export function __resetPlaygroundTokenCache(): void {
  cache.clear();
}
