import type { NextConfig } from "next";

// ── Security headers (v50 security-headers-csp) ────────────────────────────────
// Pragmatic static CSP (Tin's freeze 2026-06-26): script-src allows 'unsafe-inline'
// because the root layout ships an inline no-flash theme <script> and Next's App
// Router emits inline hydration scripts; a nonce upgrade is a tracked SPEC delta
// (deferred until the task-6 browser harness can verify enforcement). Every other
// structural protection (clickjacking, object/embed, base-uri, mixed-content,
// cross-origin form posts) is enforced. Applied to ALL routes via "/:path*".
const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  "connect-src 'self'",
  "frame-ancestors 'none'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "upgrade-insecure-requests",
].join("; ");

const SECURITY_HEADERS: Array<{ key: string; value: string }> = [
  { key: "Content-Security-Policy", value: CONTENT_SECURITY_POLICY },
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), interest-cohort=()",
  },
];

const nextConfig: NextConfig = {
  async headers() {
    return [{ source: "/:path*", headers: SECURITY_HEADERS }];
  },
};

export default nextConfig;
