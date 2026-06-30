"use client";

/**
 * components/voice/VoiceTopBar.tsx — top bar for the voice playground.
 *
 * Shows the h1 title, a session cost pill, and an abort button when a
 * network operation is in-flight. A phase-indicator span (data-testid)
 * is rendered while phase is neither "idle" nor "error".
 */

import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Phase } from "./voice-types";

const PHASE_LABELS: Record<Phase, string> = {
  idle: "",
  recording: "Recording…",
  transcribing: "Transcribing…",
  thinking: "Thinking…",
  speaking: "Speaking…",
  error: "",
};

export interface VoiceTopBarProps {
  phase: Phase;
  sessionTokens: number;
  sessionCost: number | null;
  onAbort: () => void;
}

export function VoiceTopBar({
  phase,
  sessionTokens,
  sessionCost,
  onAbort,
}: VoiceTopBarProps) {
  const isActive = phase !== "idle" && phase !== "error";

  return (
    <header className="flex items-center gap-3 border-b border-border bg-background px-4 py-2.5">
      <h1 className="text-base font-semibold text-foreground">Voice</h1>

      {isActive && (
        <span
          data-testid="phase-indicator"
          className="rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary"
        >
          {PHASE_LABELS[phase]}
        </span>
      )}

      <div className="ml-auto flex items-center gap-2">
        <span
          data-testid="cost-readout"
          aria-label="Session token usage"
          className="rounded-full border border-border px-3 py-1 text-xs text-muted-foreground"
        >
          {sessionTokens > 0 ? (
            <>
              <span className="font-medium text-foreground">
                {sessionTokens.toLocaleString()} tokens
              </span>
              {sessionCost != null && Number.isFinite(sessionCost) && (
                <span>
                  {" · "}
                  <span className="font-medium text-foreground">
                    ${sessionCost.toFixed(4)}
                  </span>
                </span>
              )}
            </>
          ) : (
            "Session cost —"
          )}
        </span>

        {isActive && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Abort"
            onClick={onAbort}
            className="size-7 text-muted-foreground"
          >
            <X className="size-3.5" aria-hidden="true" />
          </Button>
        )}
      </div>
    </header>
  );
}
