"use client";

/**
 * components/voice/VoiceInspector.tsx — right-rail controls for the voice playground.
 *
 * Houses all model/voice pickers and the session cost summary. Follows the same
 * layout pattern as InspectorPanel in the chat playground.
 *
 * Datalist suggestions come from the catalog (/admin/catalog/models) via
 * useCatalogModels(). The fields are always free-text so models absent from the
 * catalog (e.g. whisper-1, tts-1) remain usable.
 *
 * Frozen contracts:
 *   - id="stt-model-options" datalist (whisper / transcription models)
 *   - id="tts-model-options" datalist (tts / speech models)
 *   - aria-label="Chat model" input
 *   - aria-label="Voice" select (options: alloy echo fable onyx nova shimmer)
 */

import { useMemo } from "react";
import { Input } from "@/components/ui/input";
import { useCatalogModels, narrowModels } from "@/lib/hooks/use-catalog-models";

export interface VoiceInspectorProps {
  sttModel: string;
  onSttModelChange: (v: string) => void;
  chatModel: string;
  onChatModelChange: (v: string) => void;
  ttsVoice: string;
  onTtsVoiceChange: (v: string) => void;
  ttsModel: string;
  onTtsModelChange: (v: string) => void;
  ttsFormat: string;
  onTtsFormatChange: (v: string) => void;
  sessionTokens: number;
  sessionCost: number | null;
}

const TTS_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"] as const;
const TTS_FORMATS = ["mp3", "opus", "aac", "flac", "wav", "pcm"] as const;

export function VoiceInspector({
  sttModel,
  onSttModelChange,
  chatModel,
  onChatModelChange,
  ttsVoice,
  onTtsVoiceChange,
  ttsModel,
  onTtsModelChange,
  ttsFormat,
  onTtsFormatChange,
  sessionTokens,
  sessionCost,
}: VoiceInspectorProps) {
  const catalogModels = useCatalogModels();

  const sttSuggestions = useMemo(
    () => narrowModels(catalogModels, /whisper|transcrib|stt/i),
    [catalogModels],
  );

  const ttsSuggestions = useMemo(
    () => narrowModels(catalogModels, /tts|speech|audio/i),
    [catalogModels],
  );

  return (
    <aside
      className="flex h-full w-72 flex-shrink-0 flex-col gap-4 overflow-y-auto border-l border-border bg-background px-4 py-4"
      aria-label="Voice Inspector"
    >
      <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        Controls
      </span>

      {/* STT model */}
      <div className="flex flex-col gap-1.5">
        <label htmlFor="inspector-stt-model" className="text-xs font-medium text-foreground">
          STT model
        </label>
        <Input
          id="inspector-stt-model"
          type="text"
          aria-label="STT model"
          list="stt-model-options"
          value={sttModel}
          onChange={(e) => onSttModelChange(e.target.value)}
          placeholder="whisper-1"
          className="h-8 text-xs"
        />
        <datalist id="stt-model-options">
          {sttSuggestions.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
      </div>

      {/* Chat model */}
      <div className="flex flex-col gap-1.5">
        <label htmlFor="inspector-chat-model" className="text-xs font-medium text-foreground">
          Chat model
        </label>
        <Input
          id="inspector-chat-model"
          type="text"
          aria-label="Chat model"
          value={chatModel}
          onChange={(e) => onChatModelChange(e.target.value)}
          placeholder="openai/gpt-4o"
          className="h-8 text-xs"
        />
      </div>

      {/* TTS voice */}
      <div className="flex flex-col gap-1.5">
        <label htmlFor="inspector-tts-voice" className="text-xs font-medium text-foreground">
          Voice
        </label>
        <select
          id="inspector-tts-voice"
          aria-label="Voice"
          value={ttsVoice}
          onChange={(e) => onTtsVoiceChange(e.target.value)}
          className="flex h-8 w-full rounded-md border border-input bg-background px-2 py-1 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {TTS_VOICES.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      </div>

      {/* TTS model */}
      <div className="flex flex-col gap-1.5">
        <label htmlFor="inspector-tts-model" className="text-xs font-medium text-foreground">
          TTS model
        </label>
        <Input
          id="inspector-tts-model"
          type="text"
          aria-label="TTS model"
          list="tts-model-options"
          value={ttsModel}
          onChange={(e) => onTtsModelChange(e.target.value)}
          placeholder="tts-1"
          className="h-8 text-xs"
        />
        <datalist id="tts-model-options">
          {ttsSuggestions.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
      </div>

      {/* TTS format */}
      <div className="flex flex-col gap-1.5">
        <label htmlFor="inspector-tts-format" className="text-xs font-medium text-foreground">
          Format
        </label>
        <select
          id="inspector-tts-format"
          aria-label="Audio format"
          value={ttsFormat}
          onChange={(e) => onTtsFormatChange(e.target.value)}
          className="flex h-8 w-full rounded-md border border-input bg-background px-2 py-1 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {TTS_FORMATS.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      </div>

      {/* Session cost summary */}
      <div className="mt-auto border-t border-border pt-4">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Session
        </span>
        <div className="mt-2 text-xs text-muted-foreground">
          {sessionTokens > 0 ? (
            <>
              <p>
                <span className="font-medium text-foreground">
                  {sessionTokens.toLocaleString()}
                </span>{" "}
                tokens
              </p>
              {sessionCost != null && Number.isFinite(sessionCost) && (
                <p className="mt-1">
                  <span className="font-medium text-foreground">
                    ${sessionCost.toFixed(4)}
                  </span>{" "}
                  total
                </p>
              )}
            </>
          ) : (
            <p>No usage yet</p>
          )}
        </div>
      </div>
    </aside>
  );
}
