/**
 * lib/bff-client.ts — client-side adapter for BFF route handlers
 *
 * All calls go to same-origin /api/gw/* or /api/auth/* with
 * credentials:"include" so cookies are sent automatically.
 * No Authorization header is ever constructed or read client-side.
 * No localStorage read or write.
 *
 * On 401 from /api/gw/*: fires window.location.href = "/login" when the body carries
 * ERR_AUTH_SESSION_EXPIRED (a genuine session expiry, signalled by the BFF for
 * control-plane 401s) OR ERR_AUTH_NO_SESSION (the cookie is absent entirely —
 * app/api/gw/[...path]/route.ts:136 — e.g. a client-side query firing after the
 * session cookie was cleared/never existed; proxy.ts's route guard only covers a
 * fresh page navigation to /app/*, not an in-flight client fetch from an already-
 * mounted page). A data-plane (/v1/*) 401 is surfaced as a thrown BffError without
 * redirecting — the caller handles it.
 */

/**
 * Resolve the app base URL — absolute in Node.js (required by undici/fetch),
 * relative prefix ("") in browser (window.location provides the base).
 */
/**
 * Returns the absolute base URL for same-origin BFF fetches.
 *
 * - In the browser: empty string so relative URLs work via the browser's
 *   native URL resolution (window.location provides the origin).
 * - In Node.js (SSR / tests): must be absolute because Node's fetch
 *   implementation (undici) does not accept relative URLs.
 *   Uses NEXT_PUBLIC_APP_URL (set in tests-bff/setup.ts) or falls back.
 */
function appBase(): string {
  // typeof window is undefined in Node/SSR; jsdom sets window but
  // Node's global fetch (undici) still rejects relative URLs.
  // Detect "real browser" vs Node/jsdom by checking window.document.URL:
  // in jsdom it is set to the configured origin; in a real browser it is also
  // set. However undici is the fetch implementation in both Node and jsdom-vitest.
  // The safest check: if process is defined and process.versions.node exists,
  // we are in Node (vitest jsdom still runs in Node).
  if (typeof process !== "undefined" && process.versions?.node) {
    return (
      process.env.NEXT_PUBLIC_APP_URL ??
      process.env.VERCEL_URL ??
      "http://localhost:3000"
    );
  }
  return "";
}

// BffError / ProblemDetail are defined in the resilient-fetch core (the lowest
// layer) and re-exported here so every existing `@/lib/bff-client` import keeps
// resolving and there is ONE error class app-wide (instanceof holds).
import { resilientFetch, BffError, type ProblemDetail } from "./resilient-fetch";
export { BffError };
export type { ProblemDetail };

/**
 * Build the `/login?next=<encoded>` bounce URL preserving the caller's return path
 * (device-activate-page M1). Defined in this low BFF layer so the 401 bounce below and
 * the /activate page + login page share ONE implementation (with sanitizeNext/loginNextTarget).
 * Never preserves an already-`/login` location (avoids a redirect loop).
 */
export function buildLoginBounceUrl(pathAndSearch: string): string {
  // Preserve ONLY a meaningful, same-origin, non-root, non-login return path. An empty,
  // root ("/"), scheme-relative ("//host"), already-/login, or non-string value yields a
  // bare "/login" — there is nothing worth returning to (and no open-redirect surface).
  if (
    typeof pathAndSearch !== "string" ||
    !pathAndSearch.startsWith("/") ||
    pathAndSearch.startsWith("//") ||
    pathAndSearch.startsWith("/login") ||
    pathAndSearch === "/"
  ) {
    return "/login";
  }
  return `/login?next=${encodeURIComponent(pathAndSearch)}`;
}

/** Default post-login destination when no valid same-origin `next` is present. */
export const DEFAULT_POST_LOGIN = "/app/keys";

// Reject CR/LF/tab/other control chars (0x00-0x1f, 0x7f) — header/redirect smuggling
// vectors. A char-code scan avoids embedding raw control bytes in this source file.
function hasControlChar(s: string): boolean {
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c <= 0x1f || c === 0x7f) return true;
  }
  return false;
}

/**
 * Open-redirect guard (device-activate-page R-D): return `raw` ONLY when it is a
 * same-origin RELATIVE path, else null. Rejects absolute URLs, scheme-relative `//host`,
 * any backslash (browsers normalize `\` -> `/`, so `/\evil` becomes `//evil`), a
 * leading-whitespace-smuggled scheme, embedded control chars, and anything not rooted at `/`.
 * Lives beside buildLoginBounceUrl so all /login redirect-safety logic is in one place.
 */
export function sanitizeNext(raw: string | null | undefined): string | null {
  if (typeof raw !== "string" || raw.length === 0) return null;
  if (raw.includes("\\")) return null; // backslash-smuggled -> normalizes to //host
  if (!raw.startsWith("/")) return null; // must be rooted-relative (rejects scheme, ws-smuggle)
  if (raw.startsWith("//")) return null; // scheme-relative -> off-origin
  if (hasControlChar(raw)) return null; // CR/LF/tab/control smuggling
  return raw;
}

/** The validated post-login destination: a same-origin `next`, else the default. */
export function loginNextTarget(
  rawNext: string | null | undefined,
  fallback: string = DEFAULT_POST_LOGIN,
): string {
  return sanitizeNext(rawNext) ?? fallback;
}

async function handleBffResponse<T>(res: Response, isAuthEndpoint = false): Promise<T> {
  if (res.status === 401 && !isAuthEndpoint) {
    let body: ProblemDetail;
    try {
      body = (await res.json()) as ProblemDetail;
    } catch {
      body = { title: "Unauthorized", status: 401 };
    }
    // Bounce to /login on a genuine session expiry OR a never-authenticated call
    // (no session cookie at all). The BFF emits ERR_AUTH_SESSION_EXPIRED (clearing
    // the cookie) or ERR_AUTH_NO_SESSION (cookie absent, cookie never cleared —
    // there is nothing to clear) solely for control-plane (/admin/*) 401s. A
    // data-plane (/v1/*) 401 carries the upstream's own code (e.g.
    // ERR_AUTH_INVALID_KEY) and must NOT log the user out — the caller handles it
    // (fail-open picker / error state).
    const bodyCode = (body as { code?: string }).code;
    if (
      typeof window !== "undefined" &&
      (bodyCode === "ERR_AUTH_SESSION_EXPIRED" || bodyCode === "ERR_AUTH_NO_SESSION")
    ) {
      // Preserve the current path+search so login can return the user here (e.g. an
      // in-flight /activate preview that hit an expired session). Defensive `?? ""` —
      // some environments expose a partial location; buildLoginBounceUrl falls back to a
      // bare "/login" for any non-meaningful path. login re-validates via sanitizeNext.
      const returnTo = `${window.location.pathname ?? ""}${window.location.search ?? ""}`;
      window.location.href = buildLoginBounceUrl(returnTo);
    }
    throw new BffError(401, body);
  }

  if (!res.ok) {
    let body: ProblemDetail;
    try {
      body = (await res.json()) as ProblemDetail;
    } catch {
      body = { title: "Request failed", status: res.status };
    }
    throw new BffError(res.status, body);
  }

  if (res.status === 204) {
    return undefined as unknown as T;
  }

  return res.json() as Promise<T>;
}

/** GET /api/gw/<path> — no Authorization header; cookie sent via credentials */
export async function bffGet<T>(path: string): Promise<T> {
  const res = await resilientFetch(`${appBase()}/api/gw${path}`, {
    method: "GET",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
  });
  return handleBffResponse<T>(res);
}

/** POST /api/gw/<path> — no Authorization header */
export async function bffPost<T>(path: string, body: unknown): Promise<T> {
  const res = await resilientFetch(`${appBase()}/api/gw${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleBffResponse<T>(res);
}

/** PUT /api/gw/<path> */
export async function bffPut<T>(path: string, body: unknown): Promise<T> {
  const res = await resilientFetch(`${appBase()}/api/gw${path}`, {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleBffResponse<T>(res);
}

/** PATCH /api/gw/<path> — same pattern as bffPut but method: "PATCH" */
export async function bffPatch<T>(path: string, body: unknown): Promise<T> {
  const res = await resilientFetch(`${appBase()}/api/gw${path}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleBffResponse<T>(res);
}

/** DELETE /api/gw/<path> */
export async function bffDelete(path: string): Promise<void> {
  const res = await resilientFetch(`${appBase()}/api/gw${path}`, {
    method: "DELETE",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
  });
  return handleBffResponse<void>(res);
}

/** GET /api/auth/<path> — direct auth-path read (suppresses the 401→/login redirect) */
export async function bffAuthGet<T>(path: string): Promise<T> {
  const res = await resilientFetch(`${appBase()}/api/auth/${path}`, {
    method: "GET",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
  });
  return handleBffResponse<T>(res, true);
}

/** POST /api/auth/<path> — used for login, signup, logout */
export async function bffAuthPost<T>(path: string, body: unknown): Promise<T> {
  const res = await resilientFetch(`${appBase()}/api/auth/${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleBffResponse<T>(res, true);
}
