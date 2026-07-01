"use client";

/**
 * components/voice/VoiceComposer.tsx — voice input area.
 *
 * Three parallel input paths:
 *   STT-only:   audio file upload → "Transcribe" → adds user turn with transcript
 *   TTS-only:   text input → "Speak" → adds assistant turn with audio
 *   Full loop:  text input (used as transcript) → "Voice Turn" → chat + TTS
 *
 * The file input is visible (not display:none) so fireEvent.change can reach it
 * in tests. The error state (role="alert") renders in this component so it is
 * announced immediately without navigating to a different pane.
 *
 * Mic unavailability: when micAvailable=false (jsdom / no getUserMedia), a
 * notice is shown instead of the record button.
 */

import { useState, type FormEvent } from "react";
import { Mic, MicOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorState } from "@/components/ui/states";
import type { Phase } from "./voice-types";

export interface VoiceComposerProps {
  phase: Phase;
  errorTitle: string | null;
  micAvailable: boolean;
  /** True while the mic recorder is actively capturing — controls aria-pressed. */
  isRecording: boolean;
  onTranscribe: (file: File) => Promise<void>;
  onSpeak: (text: string) => Promise<void>;
  onVoiceTurn: (transcript: string) => Promise<void>;
  /** Called on pointerDown on the mic button — starts the recorder. */
  onRecordStart: () => void;
  /** Called on pointerUp / pointerLeave / pointerCancel — stops the recorder. */
  onRecordStop: () => void;
}

export function VoiceComposer({
  phase,
  errorTitle,
  micAvailable,
  isRecording,
  onTranscribe,
  onSpeak,
  onVoiceTurn,
  onRecordStart,
  onRecordStop,
}: VoiceComposerProps) {
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");

  const isBusy = phase !== "idle" && phase !== "error";

  async function handleTranscribeSubmit(e: FormEvent) {
    e.preventDefault();
    if (!file || isBusy) return;
    await onTranscribe(file);
  }

  async function handleSpeakClick() {
    const trimmed = text.trim();
    if (!trimmed || isBusy) return;
    await onSpeak(trimmed);
  }

  async function handleVoiceTurnClick() {
    const trimmed = text.trim();
    if (!trimmed || isBusy) return;
    await onVoiceTurn(trimmed);
  }

  return (
    <div className="border-t border-border bg-background px-4 py-3">
      {/* Error state — role="alert" so screen readers announce immediately */}
      {phase === "error" && errorTitle && (
        <div className="mb-3">
          <ErrorState title={errorTitle} />
        </div>
      )}

      {/* STT-only row */}
      <form onSubmit={handleTranscribeSubmit} className="mb-3 flex items-center gap-2">
        <label htmlFor="composer-stt-file" className="sr-only">
          Audio file
        </label>
        {/* NOT display:none — must be reachable by fireEvent.change in tests */}
        <Input
          id="composer-stt-file"
          type="file"
          accept="audio/*"
          aria-label="Audio file"
          className="flex-1 cursor-pointer"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <Button
          type="submit"
          size="sm"
          disabled={isBusy || !file}
          aria-disabled={isBusy || !file}
        >
          Transcribe
        </Button>
      </form>

      {/* Text + voice-turn row */}
      <div className="flex items-center gap-2">
        <Input
          type="text"
          aria-label="Text to speak"
          placeholder="Type text to speak or as voice turn transcript…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="flex-1"
          disabled={isBusy}
        />

        {/* Mic button — only when mic is available (real or injected seam).
            Hold to record: pointerDown starts capture, pointerUp/Leave/Cancel
            stops it and feeds the blob into the STT→chat→TTS pipeline.
            aria-pressed tracks recording state for screen readers.
            Disabled for other busy phases but ENABLED during recording so the
            user can release it. */}
        {micAvailable ? (
          <Button
            type="button"
            size="sm"
            variant={isRecording ? "default" : "outline"}
            aria-label={isRecording ? "Recording…" : "Hold to record"}
            aria-pressed={isRecording}
            disabled={isBusy && !isRecording}
            title="Hold to record"
            onPointerDown={onRecordStart}
            onPointerUp={onRecordStop}
            onPointerLeave={onRecordStop}
            onPointerCancel={onRecordStop}
          >
            <Mic className="size-4" aria-hidden="true" />
          </Button>
        ) : (
          <span
            className="flex items-center gap-1 rounded-md border border-border px-2.5 py-1.5 text-xs text-muted-foreground"
            title="Microphone not available in this browser"
          >
            <MicOff className="size-3.5" aria-hidden="true" />
            <span>Mic not available</span>
          </span>
        )}

        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={isBusy || !text.trim()}
          aria-disabled={isBusy || !text.trim()}
          onClick={handleSpeakClick}
        >
          Speak
        </Button>

        <Button
          type="button"
          size="sm"
          disabled={isBusy || !text.trim()}
          aria-disabled={isBusy || !text.trim()}
          onClick={handleVoiceTurnClick}
        >
          Voice Turn
        </Button>
      </div>
    </div>
  );
}
