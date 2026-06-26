/**
 * lib/resilient-fetch.ts — the hardened BFF→gateway fetch core (v50)
 *
 * Wraps `fetch` with the IO-rule trio raised to the dashboard BFF tier:
 *   - timeout      (AbortController; never hang)
 *   - bounded retry (idempotent GET/HEAD only; transient failures; backoff+jitter)
 *   - circuit-breaker (per-origin; OPEN → fail fast → HALF-OPEN probe → close)
 *
 * Runtime-agnostic: holds NO window/document reference, so both the browser
 * adapters (lib/bff-client.ts) and the server proxy (app/api/gw/[...path]) call it.
 *
 * BffError / ProblemDetail are defined HERE (the lowest layer) and re-exported by
 * bff-client.ts so there is a SINGLE error class across the app (instanceof holds).
 */

/** problem+json shape returned by the BFF/gateway for error responses. */
export interface ProblemDetail {
  type?: string;
  title: string;
  status: number;
  code?: string;
}

/** The single typed error every BFF call surfaces (transport + HTTP). */
export class BffError extends Error {
  constructor(
    public readonly status: number,
    public readonly problem: ProblemDetail
  ) {
    super(problem.title ?? `HTTP ${status}`);
    this.name = "BffError";
  }
}

export interface ResilientFetchOptions {
  /** Abort the attempt after this many ms (default: env BFF_FETCH_TIMEOUT_MS or 10_000). */
  timeoutMs?: number;
  /** Max retries for idempotent methods (default: env BFF_FETCH_MAX_RETRIES or 2). */
  maxRetries?: number;
  /** Upstream statuses that trigger a retry on idempotent methods (default [502,503,504]). */
  retryableStatuses?: number[];
  /** Circuit-breaker key (default: the URL origin). */
  circuitKey?: string;
}

// ── config (env-overridable; fail-open parse) ──────────────────────────────────
function envNum(name: string, fallback: number): number {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return fallback;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}

const DEFAULT_RETRYABLE_STATUSES = [502, 503, 504];

interface BreakerConfig {
  failureThreshold: number;
  cooldownMs: number;
}

function readBreakerConfigFromEnv(): BreakerConfig {
  return {
    failureThreshold: envNum("BFF_BREAKER_THRESHOLD", 5),
    cooldownMs: envNum("BFF_BREAKER_COOLDOWN_MS", 30_000),
  };
}

let breakerConfig: BreakerConfig = readBreakerConfigFromEnv();

/** Fail-open: coerce any unusable config back to safe defaults (M6). */
function effectiveBreakerConfig(): BreakerConfig {
  const ft =
    Number.isFinite(breakerConfig.failureThreshold) && breakerConfig.failureThreshold > 0
      ? breakerConfig.failureThreshold
      : 5;
  const cd =
    Number.isFinite(breakerConfig.cooldownMs) && breakerConfig.cooldownMs > 0
      ? breakerConfig.cooldownMs
      : 30_000;
  return { failureThreshold: ft, cooldownMs: cd };
}

// ── circuit-breaker state (in-memory, per-runtime) ─────────────────────────────
interface BreakerEntry {
  failures: number;
  openedAt: number | null; // ms epoch when OPENed; null = CLOSED
  probing: boolean; // a half-open trial is currently in flight
}

const breakers = new Map<string, BreakerEntry>();

function entryFor(key: string): BreakerEntry {
  let e = breakers.get(key);
  if (!e) {
    e = { failures: 0, openedAt: null, probing: false };
    breakers.set(key, e);
  }
  return e;
}

/**
 * Returns true when no attempt is allowed (circuit OPEN + cooling, or a half-open
 * probe already in flight). When the cooldown has elapsed this CLAIMS the single
 * half-open probe slot (sets `probing`) and returns false for exactly one caller;
 * concurrent callers see `probing` and stay blocked until the probe resolves —
 * enforcing the "one trial" invariant even under parallel requests.
 */
function isCircuitOpen(key: string): boolean {
  const e = breakers.get(key);
  if (!e || e.openedAt === null) return false;
  const { cooldownMs } = effectiveBreakerConfig();
  if (Date.now() - e.openedAt < cooldownMs) return true; // still cooling down
  if (e.probing) return true; // another half-open trial is already in flight
  e.probing = true; // claim the single probe slot
  return false;
}

function recordSuccess(key: string): void {
  const e = entryFor(key);
  e.failures = 0;
  e.openedAt = null;
  e.probing = false;
}

function recordFailure(key: string): void {
  const e = entryFor(key);
  const { failureThreshold } = effectiveBreakerConfig();
  if (e.openedAt !== null) {
    // A failed half-open trial re-opens the circuit and frees the probe slot.
    e.openedAt = Date.now();
    e.probing = false;
    return;
  }
  e.failures += 1;
  if (e.failures >= failureThreshold) {
    e.openedAt = Date.now();
  }
}

// ── helpers ────────────────────────────────────────────────────────────────────
function originOf(url: string): string {
  try {
    return new URL(url).origin;
  } catch {
    return url;
  }
}

function isAbortError(err: unknown): boolean {
  return err instanceof Error && (err.name === "AbortError" || err.name === "TimeoutError");
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function backoffDelayMs(attempt: number): number {
  const base = envNum("BFF_FETCH_BACKOFF_MS", 200);
  const exp = base * Math.pow(2, attempt);
  const jitter = Math.random() * base;
  return Math.min(exp + jitter, 10_000);
}

function timeoutError(): BffError {
  return new BffError(504, {
    title: "Upstream timed out",
    status: 504,
    code: "ERR_BFF_TIMEOUT",
  });
}

function networkError(): BffError {
  return new BffError(502, {
    title: "Upstream unreachable",
    status: 502,
    code: "ERR_BFF_NETWORK",
  });
}

function circuitOpenError(): BffError {
  return new BffError(503, {
    title: "Service temporarily unavailable",
    status: 503,
    code: "ERR_BFF_CIRCUIT_OPEN",
  });
}

// NOTE: the timeout owns the abort signal. Any `signal` on `init` is replaced by
// the internal timeout controller (no current caller passes one). If external
// cancellation is ever needed, compose the two signals here.
async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

// ── the core ───────────────────────────────────────────────────────────────────
/**
 * Resilient fetch. Returns the raw Response for any HTTP response (the caller's
 * handler interprets 2xx/4xx/5xx). THROWS BffError only for transport failure:
 * timeout (504), open circuit (503, no attempt), or network after retries (502).
 */
export async function resilientFetch(
  input: string | URL,
  init: RequestInit = {},
  opts: ResilientFetchOptions = {}
): Promise<Response> {
  const url = typeof input === "string" ? input : input.toString();
  const key = opts.circuitKey ?? originOf(url);
  const method = (init.method ?? "GET").toUpperCase();
  const idempotent = method === "GET" || method === "HEAD";
  const maxRetries = idempotent ? opts.maxRetries ?? envNum("BFF_FETCH_MAX_RETRIES", 2) : 0;
  const timeoutMs = opts.timeoutMs ?? envNum("BFF_FETCH_TIMEOUT_MS", 10_000);
  const retryable = opts.retryableStatuses ?? DEFAULT_RETRYABLE_STATUSES;

  if (isCircuitOpen(key)) {
    throw circuitOpenError();
  }

  let lastError: BffError | null = null;

  for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
    try {
      const res = await fetchWithTimeout(url, init, timeoutMs);

      if (retryable.includes(res.status) && idempotent && attempt < maxRetries) {
        await sleep(backoffDelayMs(attempt));
        continue;
      }

      // Terminal HTTP response. 5xx counts as an upstream failure for the breaker;
      // 4xx/2xx/3xx are not upstream faults (client error or success).
      if (res.status >= 500) {
        recordFailure(key);
      } else {
        recordSuccess(key);
      }
      return res;
    } catch (err) {
      lastError = isAbortError(err) ? timeoutError() : networkError();
      if (idempotent && attempt < maxRetries) {
        await sleep(backoffDelayMs(attempt));
        continue;
      }
      recordFailure(key);
      throw lastError;
    }
  }

  // Unreachable: the loop always returns or throws. Defensive only.
  recordFailure(key);
  throw lastError ?? networkError();
}

// ── test seams (NOT for production callers) ────────────────────────────────────
/** Clear all circuit-breaker state. */
export function __resetBreakers(): void {
  breakers.clear();
}

/** Override breaker config for a test; restores from env on __resetBreakers? No — explicit. */
export function __setBreakerConfigForTest(partial: Partial<BreakerConfig>): void {
  breakerConfig = { ...breakerConfig, ...partial };
}
