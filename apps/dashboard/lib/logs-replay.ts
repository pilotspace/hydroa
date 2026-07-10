/**
 * lib/logs-replay.ts — sessionStorage-mediated chat-replay handoff
 * (logs-explorer-ui TASK.md §3 CONTRACT M6 / §0 Ground Issue #2).
 *
 * One-way: LogDetailDrawer's Replay action writes a ReplayPayload, navigates to
 * /app/chat, and ChatWorkspace consumes it exactly once on mount then clears it —
 * so back-navigation never re-triggers a stale replay. No new BFF endpoint, no
 * URL-embedded payload (avoids leaking PII-adjacent captured content into browser
 * history/referrers/URL length limits — see the frozen contract's Issue #2).
 *
 * `window.sessionStorage` is read/written directly (never the bare `sessionStorage`
 * global) — Node's own built-in Web Storage globals can shadow jsdom's origin-scoped
 * implementation under vitest (the same class of gotcha tests/setup.ts documents and
 * polyfills for `localStorage`); qualifying through `window` sidesteps it without
 * needing a parallel polyfill. Every call is SSR-safe (`typeof window === "undefined"`
 * guards a no-op/null return — this module is only ever invoked from inside a
 * useEffect/event-handler in "use client" components, never at module/render scope)
 * and every read/write is wrapped so a disabled/full/blocked storage degrades
 * honestly (no throw, no crash) rather than breaking navigation or the composer.
 */

export const REPLAY_STORAGE_KEY = "hydroa:chat-replay";

export interface ReplayPayload {
  /** Flattened text content from request_body.messages (user-role turns), text-only (M8). */
  text: string;
  /** The log's model_id AS CAPTURED — catalog-validity is checked by the READER
   *  (ChatWorkspace), not pre-resolved at write time, so a catalog change between
   *  the write and the read is still honored correctly. */
  modelId: string;
  /** true if the captured request contained non-text (e.g. image) content that was
   *  dropped during flattening (M8) — never a silent partial replay. */
  degraded: boolean;
}

function isReplayPayload(value: unknown): value is ReplayPayload {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.text === "string" &&
    typeof v.modelId === "string" &&
    typeof v.degraded === "boolean"
  );
}

/** Write the handoff payload before navigating to /app/chat. Best-effort; never throws. */
export function writeReplayPayload(payload: ReplayPayload): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(REPLAY_STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // honest degrade — storage disabled/full; the composer simply won't prefill
  }
}

/**
 * Read + immediately clear the handoff payload. Idempotent — returns null on every
 * call after the first successful read (or when nothing was written, or the stored
 * value is malformed/foreign JSON).
 */
export function consumeReplayPayload(): ReplayPayload | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(REPLAY_STORAGE_KEY);
    if (raw === null) return null;
    window.sessionStorage.removeItem(REPLAY_STORAGE_KEY);
    const parsed: unknown = JSON.parse(raw);
    return isReplayPayload(parsed) ? parsed : null;
  } catch {
    return null;
  }
}
