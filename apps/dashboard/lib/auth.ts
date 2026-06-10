/**
 * lib/auth.ts — localStorage helpers, token decode, expiry check
 *
 * Safety: JWT is stored in localStorage (XSS risk acknowledged — see TASK.md §1 ⚠).
 * Production upgrade path: replace with httpOnly-cookie BFF.
 */

const TOKEN_KEY = "ai_proxy_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

/**
 * Decodes the JWT payload without verifying the signature.
 * Returns the exp claim (Unix seconds) or null if malformed.
 */
function decodeExp(token: string): number | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const raw = parts[1]
      .replace(/-/g, "+")
      .replace(/_/g, "/");
    // atob handles missing padding gracefully in modern environments
    const payload = JSON.parse(atob(raw)) as { exp?: number };
    return typeof payload.exp === "number" ? payload.exp : null;
  } catch {
    return null;
  }
}

/**
 * Returns true if the token is present AND not yet expired.
 * Client-side only; the gateway always does authoritative validation.
 */
export function isTokenValid(token: string | null): boolean {
  if (!token) return false;
  const exp = decodeExp(token);
  if (exp === null) return true; // no exp claim → assume valid, gateway enforces
  return exp > Math.floor(Date.now() / 1000);
}
