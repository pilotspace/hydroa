"use client";

/**
 * components/chat/CostReadout.tsx — v40 chat-cost-readout.
 *
 * Header pill showing chat token usage: the running session total plus the
 * latest turn's tokens. HONEST by design — token counts ONLY, never a "$" price
 * (the stream usage frame carries token counts, not price, and there is no
 * client-side per-model pricing, so a dollar figure would be fabricated). Before
 * any usage arrives it shows a placeholder, never a fabricated 0-cost.
 */

import { cn } from "@/lib/cn";
import type { Usage } from "@/lib/hooks/use-chat-stream";

export interface CostReadoutProps {
  sessionTokens: number;
  lastTurn?: Usage;
  /** Running sum of per-turn real costs. Absent (null/undefined) until a priced turn completes. */
  sessionCost?: number | null;
  className?: string;
}

export function CostReadout({ sessionTokens, lastTurn, sessionCost, className }: CostReadoutProps) {
  const hasUsage = sessionTokens > 0;
  return (
    <span
      data-testid="cost-readout"
      data-slot="session-cost"
      aria-label="Session token usage"
      className={cn(
        "rounded-full border border-border px-3 py-1 text-xs text-muted-foreground",
        className,
      )}
    >
      {hasUsage ? (
        <>
          <span className="font-medium text-foreground">{sessionTokens.toLocaleString()} tokens</span>
          {lastTurn ? (
            <span> · last {(lastTurn.prompt_tokens + lastTurn.completion_tokens).toLocaleString()}</span>
          ) : null}
          {sessionCost != null && Number.isFinite(sessionCost) ? (
            <span> · <span className="font-medium text-foreground">${sessionCost.toFixed(4)}</span> session</span>
          ) : null}
        </>
      ) : (
        "Session cost —"
      )}
    </span>
  );
}
