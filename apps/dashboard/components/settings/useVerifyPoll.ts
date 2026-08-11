"use client";

/**
 * useVerifyPoll — the bounded, fail-safe auto-poll engine for the domain-claims
 * console (dns-verify-softeners TASK.md §3 CONTRACT — FROZEN @ v2).
 *
 * A single interval re-checks each ELIGIBLE pending claim by calling the caller's
 * `verify(id)` (which hits the FROZEN POST /admin/domain-claims/{id}/verify). It
 * NEVER writes the seal itself — a 200 is handed back through `onVerified` so the
 * seal transitions ONLY through the existing verify-success path (frozen R2). The
 * softening is entirely additive: the manual "Verify now" button and its loud
 * alert are untouched (owned by the frozen domain-claims-console suite).
 *
 * Design-for-failure (CLAUDE.md): every poll is bounded —
 *   - CEILING: stop polling a claim after `ceilingMs` (default ~15 min) so a
 *     never-propagating record can't spin forever.
 *   - VISIBILITY: skip every tick while `document.hidden` (a backgrounded tab
 *     never burns the 30-rpm budget); resume automatically when visible.
 *   - BACKOFF: a 429 pauses that claim for BACKOFF_MS. The BFF drops the upstream
 *     Retry-After header (BffError carries status + body only), so we back off a
 *     fixed 60 s — guaranteed past the per-tenant 30-rpm window reset, never a
 *     hammer loop.
 *   - IN-FLIGHT GUARD: at most one outstanding verify per claim (no pile-ups).
 * A 400/503 (record not live yet / transient resolver error) is a CALM "still
 * checking" outcome that keeps polling; only a 410/409/404 is TERMINAL (stop +
 * surface an actionable message).
 */

import { useEffect, useRef, useState } from "react";

export interface VerifyPollConfig {
  intervalMs: number;
  ceilingMs: number;
}

/** The frozen §3 cadence/ceiling defaults (≈30 s / ≈15 min). */
export const DEFAULT_VERIFY_POLL: VerifyPollConfig = {
  intervalMs: 30_000,
  ceilingMs: 900_000,
};

/** Fixed 429 backoff — ≥ the per-tenant 30-rpm window reset (see header note). */
export const VERIFY_POLL_BACKOFF_MS = 60_000;

export type VerifyOutcome<TVerified> =
  | { type: "verified"; value: TVerified }
  | { type: "calm" } // 400/503 — not live yet / transient; keep polling
  | { type: "terminal"; message: string } // 410/409/404 — stop + actionable alert
  | { type: "backoff" } // 429 — pause this claim ≥ the retry window
  | { type: "error" }; // network/other — treat as calm (self-heals next tick)

export type PollRowStatus =
  | { kind: "checking" } // calm "still checking" indicator
  | { kind: "terminal"; message: string }; // loud actionable alert; poll stopped

const now = () => Date.now();

/**
 * Drive the auto-poll. `pollableIds` are the claim ids currently eligible
 * (status="pending" AND sealState()="pending" — verified/expired already excluded
 * by the caller). Returns the per-claim UI status to render.
 */
export function useVerifyPoll<TVerified>(params: {
  pollableIds: string[];
  config: VerifyPollConfig;
  verify: (id: string) => Promise<VerifyOutcome<TVerified>>;
  onVerified: (value: TVerified) => void;
}): Record<string, PollRowStatus> {
  const { pollableIds, config } = params;
  const [rowStatus, setRowStatus] = useState<Record<string, PollRowStatus>>({});

  // Latest values the single long-lived interval reads (avoids re-creating it).
  const idsRef = useRef(pollableIds);
  const verifyRef = useRef(params.verify);
  const onVerifiedRef = useRef(params.onVerified);
  const ceilingRef = useRef(config.ceilingMs);
  // Refreshed AFTER commit, not during render. These four assignments used to sit
  // bare in the render body, which `react-hooks/refs` reports as an error: a render
  // can be started and thrown away under concurrent React, and a ref written by a
  // discarded render keeps a value that never became UI. Every reader here runs
  // post-commit — the interval below fires on a timer, and the `idsKey` effect is
  // declared after this one, so React flushes this first — which makes the move
  // behaviour-preserving rather than a workaround for the lint rule.
  // No dependency array on purpose: "latest value" means every render.
  useEffect(() => {
    idsRef.current = pollableIds;
    verifyRef.current = params.verify;
    onVerifiedRef.current = params.onVerified;
    ceilingRef.current = config.ceilingMs;
  });

  // Per-claim lifecycle (refs — never trigger a render, survive across ticks).
  const startedAt = useRef<Map<string, number>>(new Map());
  const backoffUntil = useRef<Map<string, number>>(new Map());
  const stopped = useRef<Set<string>>(new Set());
  const inFlight = useRef<Set<string>>(new Set());

  // Keep the rendered status set in sync with the eligible ids: an eligible,
  // non-stopped claim shows the calm "checking" indicator by default; a claim
  // that left the eligible set (flipped/removed) drops its status; a terminal
  // message is preserved.
  const idsKey = pollableIds.join(",");
  useEffect(() => {
    setRowStatus((prev) => {
      const next: Record<string, PollRowStatus> = {};
      for (const id of idsRef.current) {
        if (stopped.current.has(id)) {
          if (prev[id]?.kind === "terminal") next[id] = prev[id];
        } else {
          next[id] = prev[id]?.kind === "terminal" ? prev[id] : { kind: "checking" };
        }
      }
      return next;
    });
  }, [idsKey]);

  useEffect(() => {
    const tick = () => {
      // VISIBILITY pause — skip the whole tick while backgrounded.
      if (typeof document !== "undefined" && document.hidden) return;
      const t = now();
      for (const id of idsRef.current) {
        if (stopped.current.has(id)) continue;
        if (inFlight.current.has(id)) continue;

        let started = startedAt.current.get(id);
        if (started === undefined) {
          started = t;
          startedAt.current.set(id, t);
        }
        // CEILING — stop a claim that never propagated.
        if (t - started >= ceilingRef.current) {
          stopped.current.add(id);
          continue;
        }
        // BACKOFF — a 429'd claim waits out its window.
        if (t < (backoffUntil.current.get(id) ?? 0)) continue;

        inFlight.current.add(id);
        verifyRef.current(id)
          .then((outcome) => {
            if (outcome.type === "verified") {
              stopped.current.add(id);
              setRowStatus((p) => {
                const n = { ...p };
                delete n[id];
                return n;
              });
              onVerifiedRef.current(outcome.value);
            } else if (outcome.type === "terminal") {
              stopped.current.add(id);
              setRowStatus((p) => ({ ...p, [id]: { kind: "terminal", message: outcome.message } }));
            } else if (outcome.type === "backoff") {
              backoffUntil.current.set(id, now() + VERIFY_POLL_BACKOFF_MS);
            } else {
              // calm / error — stay in the calm "checking" state, keep polling.
              setRowStatus((p) =>
                p[id]?.kind === "terminal" ? p : { ...p, [id]: { kind: "checking" } },
              );
            }
          })
          .catch(() => {
            // Defensive — verify() already maps failures to an outcome; a throw
            // here must never break the loop.
          })
          .finally(() => {
            inFlight.current.delete(id);
          });
      }
    };

    const handle = setInterval(tick, config.intervalMs);
    return () => clearInterval(handle);
  }, [config.intervalMs]);

  return rowStatus;
}
