/**
 * lib/auth.ts — localStorage helpers REMOVED per v2 BFF contract
 *
 * getToken / setToken / clearToken / isTokenValid are deleted.
 * Token transport is now exclusively via httpOnly cookie ai_proxy_session,
 * managed server-side by the BFF route handlers.
 *
 * After Build: grep over apps/dashboard/{app,components,lib} shows zero
 * localStorage references to any token key.
 */

// This file is intentionally empty — all auth helpers were localStorage-based
// and are superseded by the BFF cookie session. Kept as a module so any
// stray imports surface a clear "no exported member" TypeScript error rather
// than a MODULE_NOT_FOUND that masks the root cause.
