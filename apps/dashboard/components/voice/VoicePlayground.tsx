"use client";

/**
 * components/voice/VoicePlayground.tsx — Console-grade voice workspace.
 *
 * Three-pane layout:
 *   VoiceTopBar    — h1 "Voice", phase indicator, session cost pill, abort
 *   VoiceThread    — scrollable conversation log (role="log")
 *   VoiceComposer  — file upload (STT-only), text input (TTS-only + full loop)
 *   VoiceInspector — right-rail: STT/chat/TTS model pickers, TTS voice, cost
 *
 * Three network paths (all BFF-only, never direct gateway):
 *   STT-only:   file → POST /api/gw/v1/audio/transcriptions  → userText turn
 *   TTS-only:   text → POST /api/gw/v1/audio/speech          → audioSrc turn
 *   Full loop:  text → POST /api/gw/v1/chat/completions
 *                    → POST /api/gw/v1/audio/speech
 *                    → complete turn (userText + assistantText + audioSrc)
 *
 * An AbortController is created per-operation; "Abort" in the top bar calls
 * abort() and resets phase to "idle". Object URLs are tracked and revoked on
 * unmount. Mic detection runs once on mount and gracefully degrades when
 * navigator.mediaDevices is absent (jsdom, some browsers).
 *
 * Backward-compatible with all 7 frozen test IDs:
 *   test_stt_upload_shows_transcript  — aria-label="Audio file" + Transcribe button
 *   test_tts_plays_audio              — aria-label="Text to speak" + Speak button + audio-player
 *   test_upstream_error_shows_error_state — role="alert" on STT 502
 *   test_bff_forwards_binary_unmangled, test_bff_json_path_forwards_as_string — BFF route tests (unchanged)
 *   test_voice_nav_role_open          — NAV_ITEMS (unchanged)
 *   test_model_fields_suggest_catalog_audio_models — datalist ids (in VoiceInspector)
 */

import { useEffect, useRef, useState } from "react";
import { VoiceTopBar } from "./VoiceTopBar";
import { VoiceThread } from "./VoiceThread";
import { VoiceComposer } from "./VoiceComposer";
import { VoiceInspector } from "./VoiceInspector";
import type { Phase, VoiceTurn } from "./voice-types";

// Re-export so any existing downstream import of these types from this module
// continues to resolve without change.
export type { Phase, VoiceTurn };

/**
 * Absolute base for BFF fetches. Mirrors bff-client.ts appBase():
 * In the browser: "" (window.location provides origin).
 * In Node / jsdom (vitest): NEXT_PUBLIC_APP_URL or fallback.
 */
function bffBase(): string {
  if (typeof process !== "undefined" && process.versions?.node) {
    return process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000";
  }
  return "";
}

interface ProblemJson {
  title?: string;
  status?: number;
}

/** Extract a human-readable error title from a non-OK Response. */
async function extractErrorTitle(res: Response): Promise<string> {
  let title = `HTTP ${res.status}`;
  try {
    const p = (await res.json()) as ProblemJson;
    if (p.title) title = p.title;
  } catch {
    /* ignore JSON parse failure */
  }
  return title;
}

export function VoicePlayground() {
  const [turns, setTurns] = useState<VoiceTurn[]>([]);
  const [sttModel, setSttModel] = useState("whisper-1");
  const [chatModel, setChatModel] = useState("openai/gpt-4o");
  const [ttsVoice, setTtsVoice] = useState("alloy");
  const [ttsModel, setTtsModel] = useState("tts-1");
  const [ttsFormat, setTtsFormat] = useState("mp3");
  const [sessionTokens, setSessionTokens] = useState(0);
  const [sessionCost, setSessionCost] = useState<number | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [errorTitle, setErrorTitle] = useState<string | null>(null);
  // Lazy init — safe in "use client" components; avoids a synchronous setState-in-effect.
  // Mic stays false in jsdom (no navigator.mediaDevices) and when getUserMedia is absent.
  const [micAvailable] = useState(
    () =>
      typeof window !== "undefined" &&
      !!(
        typeof navigator !== "undefined" &&
        navigator.mediaDevices &&
        typeof navigator.mediaDevices.getUserMedia === "function"
      ),
  );

  const abortRef = useRef<AbortController | null>(null);
  const objectUrlsRef = useRef<string[]>([]);

  // Revoke all tracked object URLs on unmount.
  useEffect(() => {
    const urls = objectUrlsRef.current;
    return () => {
      for (const u of urls) {
        URL.revokeObjectURL(u);
      }
    };
  }, []);

  function trackObjectUrl(url: string): void {
    objectUrlsRef.current.push(url);
  }

  // ── STT-only path ──────────────────────────────────────────────────────────

  async function handleTranscribe(file: File): Promise<void> {
    setPhase("transcribing");
    setErrorTitle(null);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    const form = new FormData();
    form.append("file", file);
    form.append("model", sttModel);

    try {
      // DO NOT set Content-Type — the browser sets the multipart boundary.
      const res = await fetch(`${bffBase()}/api/gw/v1/audio/transcriptions`, {
        method: "POST",
        body: form,
        signal: ctrl.signal,
      });

      if (!res.ok) {
        setErrorTitle(await extractErrorTitle(res));
        setPhase("error");
        return;
      }

      const data = (await res.json()) as { text: string };
      const turn: VoiceTurn = {
        id: crypto.randomUUID(),
        userText: data.text,
        assistantText: "",
        audioSrc: null,
        meta: {
          sttModel,
          chatModel,
          ttsVoice,
          promptTokens: 0,
          completionTokens: 0,
          totalTokens: 0,
          latencyMs: 0,
        },
      };
      setTurns((prev) => [...prev, turn]);
      setPhase("idle");
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        setPhase("idle");
      } else {
        setErrorTitle("Request failed");
        setPhase("error");
      }
    }
  }

  // ── TTS-only path ──────────────────────────────────────────────────────────

  async function handleSpeak(text: string): Promise<void> {
    setPhase("speaking");
    setErrorTitle(null);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      const res = await fetch(`${bffBase()}/api/gw/v1/audio/speech`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: ttsModel,
          input: text,
          voice: ttsVoice,
          response_format: ttsFormat,
        }),
        signal: ctrl.signal,
      });

      if (!res.ok) {
        setErrorTitle(await extractErrorTitle(res));
        setPhase("error");
        return;
      }

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      trackObjectUrl(url);

      const turn: VoiceTurn = {
        id: crypto.randomUUID(),
        userText: "",
        assistantText: text,
        audioSrc: url,
        meta: {
          sttModel,
          chatModel,
          ttsVoice,
          promptTokens: 0,
          completionTokens: 0,
          totalTokens: 0,
          latencyMs: 0,
        },
      };
      setTurns((prev) => [...prev, turn]);
      setPhase("idle");
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        setPhase("idle");
      } else {
        setErrorTitle("Request failed");
        setPhase("error");
      }
    }
  }

  // ── Full voice turn loop ───────────────────────────────────────────────────

  async function handleVoiceTurn(transcript: string): Promise<void> {
    const t0 = Date.now();
    setPhase("thinking");
    setErrorTitle(null);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      // Step 1: chat completion
      const chatRes = await fetch(`${bffBase()}/api/gw/v1/chat/completions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: chatModel,
          messages: [{ role: "user", content: transcript }],
        }),
        signal: ctrl.signal,
      });

      if (!chatRes.ok) {
        setErrorTitle(await extractErrorTitle(chatRes));
        setPhase("error");
        return;
      }

      const chatData = (await chatRes.json()) as {
        choices: Array<{ message: { content: string } }>;
        usage?: {
          prompt_tokens: number;
          completion_tokens: number;
          total_tokens: number;
        };
      };
      const reply = chatData.choices[0]?.message?.content ?? "";
      const usage = chatData.usage ?? {
        prompt_tokens: 0,
        completion_tokens: 0,
        total_tokens: 0,
      };

      setSessionTokens((prev) => prev + usage.total_tokens);

      // Step 2: TTS synthesis
      setPhase("speaking");
      const ttsRes = await fetch(`${bffBase()}/api/gw/v1/audio/speech`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: ttsModel,
          input: reply,
          voice: ttsVoice,
          response_format: ttsFormat,
        }),
        signal: ctrl.signal,
      });

      if (!ttsRes.ok) {
        setErrorTitle(await extractErrorTitle(ttsRes));
        setPhase("error");
        return;
      }

      const blob = await ttsRes.blob();
      const url = URL.createObjectURL(blob);
      trackObjectUrl(url);

      const latencyMs = Date.now() - t0;
      const turn: VoiceTurn = {
        id: crypto.randomUUID(),
        userText: transcript,
        assistantText: reply,
        audioSrc: url,
        meta: {
          sttModel,
          chatModel,
          ttsVoice,
          promptTokens: usage.prompt_tokens,
          completionTokens: usage.completion_tokens,
          totalTokens: usage.total_tokens,
          latencyMs,
        },
      };
      setTurns((prev) => [...prev, turn]);
      setPhase("idle");
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        setPhase("idle");
      } else {
        setErrorTitle("Request failed");
        setPhase("error");
      }
    }
  }

  // ── Abort ──────────────────────────────────────────────────────────────────

  function handleAbort(): void {
    abortRef.current?.abort();
    setPhase("idle");
    setErrorTitle(null);
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-full min-h-0 bg-muted/30">
      {/* Left column: top bar + scrollable thread + composer */}
      <div className="flex min-w-0 flex-1 flex-col">
        <VoiceTopBar
          phase={phase}
          sessionTokens={sessionTokens}
          sessionCost={sessionCost}
          onAbort={handleAbort}
        />

        <VoiceThread turns={turns} />

        <VoiceComposer
          phase={phase}
          errorTitle={errorTitle}
          micAvailable={micAvailable}
          onTranscribe={handleTranscribe}
          onSpeak={handleSpeak}
          onVoiceTurn={handleVoiceTurn}
        />
      </div>

      {/* Right rail */}
      <VoiceInspector
        sttModel={sttModel}
        onSttModelChange={setSttModel}
        chatModel={chatModel}
        onChatModelChange={setChatModel}
        ttsVoice={ttsVoice}
        onTtsVoiceChange={setTtsVoice}
        ttsModel={ttsModel}
        onTtsModelChange={setTtsModel}
        ttsFormat={ttsFormat}
        onTtsFormatChange={setTtsFormat}
        sessionTokens={sessionTokens}
        sessionCost={sessionCost}
      />
    </div>
  );
}
