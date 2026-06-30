/**
 * Shared type definitions for the voice playground.
 * Extracted here to avoid circular imports between sub-components.
 */

export type Phase =
  | "idle"
  | "recording"
  | "transcribing"
  | "thinking"
  | "speaking"
  | "error";

export interface VoiceTurn {
  id: string;
  /** STT transcript — empty string for TTS-only turns. */
  userText: string;
  /** Chat reply (or spoken text for TTS-only turns). */
  assistantText: string;
  /** TTS blob URL — null for STT-only turns. Must be revoked on replace/unmount. */
  audioSrc: string | null;
  meta: {
    sttModel: string;
    chatModel: string;
    ttsVoice: string;
    promptTokens: number;
    completionTokens: number;
    totalTokens: number;
    latencyMs: number;
    cost?: number;
  };
}
