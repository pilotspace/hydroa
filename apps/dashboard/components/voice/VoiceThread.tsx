"use client";

/**
 * components/voice/VoiceThread.tsx — the voice conversation log.
 *
 * Renders the list of VoiceTurn objects as a scrollable conversation:
 *   - User text: div with aria-label="Transcription result"
 *   - Assistant text: rendered via MessageMarkdown
 *   - Audio replay: <audio controls data-testid="audio-player">
 *   - Per-turn metadata chip: data-testid="turn-meta"
 *
 * Empty state rendered by <Empty> when no turns exist.
 * WCAG 2.2: role="log" aria-live="polite" for screen reader announcements.
 */

import { Empty } from "@/components/ui/states";
import { MessageMarkdown } from "@/components/chat/MessageMarkdown";
import type { VoiceTurn } from "./voice-types";

export interface VoiceThreadProps {
  turns: VoiceTurn[];
  /**
   * ID of the most-recent turn that has TTS audio. When set, the matching
   * <audio> element mounts with autoPlay so the reply plays immediately.
   * play() rejection (autoplay policy) is browser-native — no JS crash.
   */
  latestTurnId?: string;
}

export function VoiceThread({ turns, latestTurnId }: VoiceThreadProps) {
  return (
    <div
      role="log"
      aria-live="polite"
      aria-label="Voice conversation"
      className="flex flex-1 flex-col gap-4 overflow-y-auto p-4"
    >
      {turns.length === 0 ? (
        <Empty
          title="No voice turns yet"
          description="Upload an audio file, type text, or use the mic to begin."
        />
      ) : (
        turns.map((turn) => (
          <div key={turn.id} className="flex flex-col gap-2">
            {/* User transcript */}
            {turn.userText && (
              <div
                aria-label="Transcription result"
                className="self-end rounded-2xl bg-primary/10 px-4 py-2.5 text-sm text-foreground"
              >
                <p>{turn.userText}</p>
              </div>
            )}

            {/* Assistant reply */}
            {turn.assistantText && (
              <div className="rounded-2xl border border-border bg-background px-4 py-2.5">
                <MessageMarkdown content={turn.assistantText} />
              </div>
            )}

            {/* Audio replay player — autoPlay on the latest audio turn only.
                autoPlay is a DOM attribute; play() rejection (browser autoplay
                policy) is handled natively and never crashes the component. */}
            {turn.audioSrc !== null && (
              <audio
                controls
                autoPlay={turn.id === latestTurnId}
                data-testid="audio-player"
                src={turn.audioSrc}
                className="w-full"
              />
            )}

            {/* Per-turn metadata chip — only shown when usage is available */}
            {(turn.meta.totalTokens > 0 || turn.meta.latencyMs > 0) && (
              <div
                data-testid="turn-meta"
                className="text-xs text-muted-foreground"
              >
                {turn.meta.chatModel} · {turn.meta.totalTokens} tok ·{" "}
                {turn.meta.latencyMs}ms
                {turn.meta.cost != null
                  ? ` · $${turn.meta.cost.toFixed(4)}`
                  : ""}
              </div>
            )}
          </div>
        ))
      )}
    </div>
  );
}
