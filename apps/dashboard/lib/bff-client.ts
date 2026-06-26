/**
 * lib/bff-client.ts — client-side adapter for BFF route handlers
 *
 * All calls go to same-origin /api/gw/* or /api/auth/* with
 * credentials:"include" so cookies are sent automatically.
 * No Authorization header is ever constructed or read client-side.
 * No localStorage read or write.
 *
 * On 401 from /api/gw/*: fires window.location.href = "/login".
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

async function handleBffResponse<T>(res: Response, isAuthEndpoint = false): Promise<T> {
  if (res.status === 401 && !isAuthEndpoint) {
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
    let body: ProblemDetail;
    try {
      body = (await res.json()) as ProblemDetail;
    } catch {
      body = { title: "Unauthorized", status: 401 };
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
