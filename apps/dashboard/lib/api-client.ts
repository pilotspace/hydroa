/**
 * lib/api-client.ts — fetch wrapper with Authorization header and 401 guard
 *
 * Reads NEXT_PUBLIC_GATEWAY_URL at call time (not at module eval) so the
 * test environment can inject it via process.env before any fetch runs.
 *
 * 401 responses: clears localStorage token and redirects to /login
 * immediately — before any caller can act on the response.
 */

import { clearToken, getToken } from "./auth";

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

function gatewayBase(): string {
  return process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://localhost:8080";
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * Handle a fetch Response, throwing ApiError on non-2xx.
 *
 * @param res        - The fetch Response object
 * @param authRoute  - true for /admin/auth/* endpoints; suppresses the
 *                     401→clear-token+redirect behaviour so that a bad
 *                     password on login surfaces as an inline error rather
 *                     than silently redirecting to /login.
 */
async function handleResponse<T>(res: Response, authRoute = false): Promise<T> {
  if (res.status === 401 && !authRoute) {
    clearToken();
    // Hard-redirect so the guard fires outside React's render cycle.
    // Guarded by typeof window to avoid SSR errors; skipped on auth routes
    // so that login-401 surfaces as an inline form error, not a redirect.
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

/** Whether a path is an unauthenticated auth endpoint (login / signup). */
function isAuthPath(path: string): boolean {
  return path.startsWith("/admin/auth/");
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${gatewayBase()}${path}`, {
    method: "GET",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
    },
  });
  return handleResponse<T>(res, isAuthPath(path));
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${gatewayBase()}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
    },
    body: JSON.stringify(body),
  });
  return handleResponse<T>(res, isAuthPath(path));
}

export async function apiDelete(path: string): Promise<void> {
  const res = await fetch(`${gatewayBase()}${path}`, {
    method: "DELETE",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
    },
  });
  return handleResponse<void>(res, isAuthPath(path));
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${gatewayBase()}${path}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
    },
    body: JSON.stringify(body),
  });
  return handleResponse<T>(res, isAuthPath(path));
}
