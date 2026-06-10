/**
 * lib/api-client.ts — fetch wrapper routing through the BFF gateway proxy
 *
 * All data calls go to same-origin /api/gw/<path> with credentials:"include"
 * so the browser sends the ai_proxy_session cookie automatically.
 * No Authorization header is ever constructed or read client-side.
 * No localStorage read or write.
 *
 * 401 responses from /api/gw/*: fires window.location.href = "/login" —
 * the BFF has already cleared the cookie at that point.
 *
 * Auth endpoints (/api/auth/*) are called directly with credentials:"include".
 */

/** problem+json shape returned by the gateway for error responses */
export interface ProblemDetail {
  type?: string;
  title: string;
  status: number;
  code?: string;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly problem: ProblemDetail
  ) {
    super(problem.title);
    this.name = "ApiError";
  }
}

/**
 * Handle a fetch Response, throwing ApiError on non-2xx.
 *
 * @param res        - The fetch Response object
 * @param authRoute  - true for /api/auth/* endpoints; suppresses the
 *                     401→redirect behaviour so that a bad password on login
 *                     surfaces as an inline error rather than silently
 *                     redirecting to /login.
 */
async function handleResponse<T>(res: Response, authRoute = false): Promise<T> {
  if (res.status === 401 && !authRoute) {
    // Hard-redirect so the guard fires outside React's render cycle.
    // BFF has already cleared the cookie at this point.
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
    const body = await res.json().catch(() => ({ title: "Unauthorized", status: 401 })) as ProblemDetail;
    throw new ApiError(401, body);
  }

  if (!res.ok) {
    let body: ProblemDetail;
    try {
      body = await res.json() as ProblemDetail;
    } catch {
      body = { title: "Request failed", status: res.status };
    }
    throw new ApiError(res.status, body);
  }

  // 204 No Content
  if (res.status === 204) {
    return undefined as unknown as T;
  }

  return res.json() as Promise<T>;
}

/** Whether a path targets a BFF auth endpoint (login / signup / logout / me). */
function isAuthPath(path: string): boolean {
  return path.startsWith("/api/auth/");
}

export async function apiGet<T>(path: string): Promise<T> {
  const url = isAuthPath(path) ? path : `/api/gw${path}`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
  });
  return handleResponse<T>(res, isAuthPath(path));
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const url = isAuthPath(path) ? path : `/api/gw${path}`;
  const res = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleResponse<T>(res, isAuthPath(path));
}

export async function apiDelete(path: string): Promise<void> {
  const url = isAuthPath(path) ? path : `/api/gw${path}`;
  const res = await fetch(url, {
    method: "DELETE",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
  });
  return handleResponse<void>(res, isAuthPath(path));
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const url = isAuthPath(path) ? path : `/api/gw${path}`;
  const res = await fetch(url, {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleResponse<T>(res, isAuthPath(path));
}
