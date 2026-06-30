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

/**
 * Minimal recorder abstraction — injectable in tests so jsdom tests can drive
 * capture without real browser media (MediaRecorder / getUserMedia).
 *
 * Default implementation in VoicePlayground wraps MediaRecorder.
 * Tests inject a fake via the `createRecorder` prop.
 */
export interface VoiceRecorder {
  /** Begin capturing audio. */
  start(): void;
  /** Stop capturing and resolve with the recorded Blob. */
  stop(): Promise<Blob>;
}

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
