/**
 * email-domain-routing — shape-aware, EXISTENCE-BLIND email-domain classification.
 *
 * domain-aware-auth-routing TASK.md §3 (FROZEN @ v1).
 *
 * THE SECURITY PROPERTY (M11, the anti-enumeration invariant):
 *   classifyEmailDomain is a PURE FUNCTION of (the typed string, the constant
 *   PUBLIC_EMAIL_DOMAINS below). Server state is NOT an input. Therefore, for
 *   any two domains in the same shape class, everything derived from this
 *   module is byte-identical regardless of whether either domain has a tenant,
 *   a verified claim, an SSO config, or any user. An adversary probing a list
 *   of 10,000 candidate customer domains learns nothing, because the product
 *   knows nothing and asks nothing.
 *
 * WHY THIS FILE HAS NO IMPORTS — and must never gain one:
 *   The invariant above is delivered STRUCTURALLY, not by an after-the-fact
 *   check. A module that imports nothing and names no IO identifier cannot
 *   reach the network on ANY input; that is a proof over all inputs, where "no
 *   fetch happened in this test" would only be a claim about the inputs the
 *   test tried. §3 forbids, in this module AND in every caller of it:
 *     - any import that performs IO (fetch, XMLHttpRequest, sendBeacon,
 *       next/navigation data fetching, dynamic import, cookie/storage reads)
 *     - any reference, direct or transitive, to
 *       resolve_verified_tenant_for_raw_domain / resolve_verified_tenant, or
 *       any tenant / user / domain-claim / SSO-config lookup
 *     - any reuse of LoginForm's SSO preflight as a classification signal
 *   Even a "harmless" util-barrel import is how IO sneaks into an IO-free
 *   module (§5 batch 2). tests/email-domain-routing.test.ts (P3) reads this
 *   file's source and fails the build if any of the above appears. A P3 failure
 *   is a SECURITY HARD-STOP, never a flaky-test retry.
 *
 * THE STANDING TEMPTATION (R1/R2): "we could just ask the server whether this
 * domain has a tenant, and route perfectly." That is precisely the rejected
 * design — the ADAPTATION ITSELF is the observable, so no amount of
 * response-shaping fixes it; the usefulness of such a call IS the leak. The
 * safety rule: the routing decision must be computable with the network cable
 * unplugged.
 */

export type EmailDomainClass = "public" | "corporate" | "unknown";

/**
 * Value-for-value mirror of
 * apps/gateway/src/gateway/domain_capture/domain/public_email_domains.py:PUBLIC_EMAIL_DOMAINS
 * (22 entries as of Ground SHA 9421827). Lower-cased apex domains only.
 *
 * This is a SECOND COPY of a security-relevant list (R-c). It is guarded, not
 * trusted: the P4 parity test reads the .py source off disk and asserts
 * set-equality, so adding an entry to either side alone FAILS the build
 * (R6 / ROUTE_PROVIDER_LIST_DRIFT). Extend both sides together, or not at all.
 */
export const PUBLIC_EMAIL_DOMAINS: ReadonlySet<string> = new Set([
  "gmail.com",
  "googlemail.com",
  "outlook.com",
  "hotmail.com",
  "live.com",
  "msn.com",
  "yahoo.com",
  "ymail.com",
  "icloud.com",
  "me.com",
  "mac.com",
  "proton.me",
  "protonmail.com",
  "aol.com",
  "gmx.com",
  "gmx.net",
  "mail.com",
  "yandex.com",
  "yandex.ru",
  "zoho.com",
  "fastmail.com",
  "pm.me",
]);

/** normalize_domain's label rule, ported literally rather than re-invented. */
const LABEL_PATTERN = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;
const MAX_DOMAIN_LENGTH = 253;

/**
 * M2 — the ONE normalizer: trim -> lowercase -> the text after the LAST "@"
 * (a value with no "@" is taken as-is). Returns "" when nothing usable.
 *
 * Supersedes the two near-duplicate derivations that existed before this task
 * (SignupForm.deriveSsoDomain trimmed only; LoginForm.resolveSsoDomain trimmed
 * + lowercased). R-d: classifying on the un-lowercased one would mis-classify
 * `Bob@GMAIL.com` as corporate — the exact drift a single normalizer prevents.
 */
export function normalizeEmailDomain(raw: string): string {
  const value = (raw ?? "").trim().toLowerCase();
  if (!value) return "";
  if (!value.includes("@")) return value;
  // NOTE: no second trim on the slice. §3 specifies exactly "trim -> lowercase
  // -> text after the LAST '@'" — the trim applies to the raw value, not again
  // to the extracted domain. This is load-bearing, not incidental: "bob@ acme.com"
  // must yield " acme.com", which fails isWellShapedDomain and so classifies
  // "unknown" (§2's malformed-domain scenario / R4 fail-safe). Re-trimming here
  // would silently "repair" a malformed address into a confident "corporate" —
  // exactly the guessing R4 forbids. (SignupForm.deriveSsoDomain DOES re-trim;
  // it is left as-is per §3, which freezes the SSO href derivation.)
  return value.slice(value.lastIndexOf("@") + 1);
}

/**
 * M4 — TS mirror of domain_validation.normalize_domain's ACCEPT rules: >= 2
 * labels, each 1-63 chars of [a-z0-9-] without a leading/trailing hyphen,
 * total <= 253 chars, not a bare IP literal.
 *
 * Deliberately STRICTER than the shipped LoginForm.validateSsoDomain regex
 * (R-g: that one accepts single-label-ish and over-long values the gateway
 * rejects). Classification must fail SAFE on anything it cannot confidently
 * shape-check, never guess.
 */
export function isWellShapedDomain(domain: string): boolean {
  // A pure SHAPE PREDICATE over an already-normalized value — it deliberately
  // does NOT trim or lowercase its input. §3 states the rules as literal label
  // constraints, and normalizeEmailDomain is the ONE normalizer (M2). Were this
  // to re-normalize, " acme.com" would be repaired into a confident "corporate"
  // instead of failing safe to "unknown" (R4).
  const candidate = domain ?? "";
  if (!candidate || candidate.length > MAX_DOMAIN_LENGTH) return false;

  const labels = candidate.split(".");
  if (labels.length < 2) return false;
  for (const label of labels) {
    if (label.length < 1 || label.length > 63) return false;
    if (!LABEL_PATTERN.test(label)) return false;
  }

  return !isIpLiteral(candidate, labels);
}

/**
 * Bare-IP-literal rejection, mirroring normalize_domain's ipaddress check.
 * Only IPv4 can survive the label rules above (an IPv6 literal contains ":",
 * which LABEL_PATTERN already rejects), so a dotted-quad check is exhaustive
 * for anything that reaches here.
 */
function isIpLiteral(candidate: string, labels: string[]): boolean {
  if (labels.length !== 4) return false;
  return labels.every((label) => /^\d{1,3}$/.test(label) && Number(label) <= 255);
}

/**
 * M1/M3/M4/M5 — classify a raw email or bare domain into exactly one class.
 *
 *   "public"    iff PUBLIC_EMAIL_DOMAINS has the normalized domain (EXACT match
 *               only — a subdomain never matches its parent, mirroring
 *               normalize_domain's no-subdomain-matches-parent rule)
 *   "corporate" iff well-shaped and not on the public list
 *   "unknown"   otherwise (empty / no domain part / malformed) <- SAFE DEFAULT
 *
 * The curated list is inherently incomplete (R-f): a public provider not on it
 * classifies as "corporate". That is a graceful MIS-ROUTE — the visitor still
 * sees every route, just ordered oddly — and must never be "fixed" by asking
 * the server what a domain is.
 */
export function classifyEmailDomain(raw: string): EmailDomainClass {
  const domain = normalizeEmailDomain(raw);
  if (!domain) return "unknown";
  if (PUBLIC_EMAIL_DOMAINS.has(domain)) return "public";
  if (isWellShapedDomain(domain)) return "corporate";
  return "unknown";
}
