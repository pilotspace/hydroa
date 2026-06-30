"use client";

/**
 * components/voice/VoicePlayground.tsx — Console-grade voice workspace.
 *
 * Three-pane layout:
 *   VoiceTopBar    — h1 "Voice", phase indicator, session cost pill, abort
 *   VoiceThread    — scrollable conversation log (role="log")
 *   VoiceComposer  — file upload (STT-only), text input (TTS-only + full loop),
 *                    hold-to-record mic button
 *   VoiceInspector — right-rail: STT/chat/TTS model pickers, TTS voice, cost
 *
 * Four network paths (all BFF-only, never direct gateway):
 *   STT-only:   file  → POST /api/gw/v1/audio/transcriptions → userText turn
 *   TTS-only:   text  → POST /api/gw/v1/audio/speech        → audioSrc turn
 *   Full loop:  text  → POST /api/gw/v1/chat/completions
 *                     → POST /api/gw/v1/audio/speech
 *                     → complete turn (userText + assistantText + audioSrc)
 *   Mic path:   blob  → STT → chat → TTS (same as full loop but audio input)
 *
 * Injectable seams (for testability in jsdom):
 *   requestMicStream — replaces navigator.mediaDevices.getUserMedia
 *   createRecorder   — replaces new MediaRecorder(stream) with a VoiceRecorder
 *
 * TTS autoplay: the latest audio turn's <audio> element mounts with autoPlay.
 * play() rejection (browser autoplay policy) is browser-native — no JS crash.
 *
 * Per-turn cost: priced from /admin/catalog/models (same formula as ChatWorkspace).
 * STT/TTS token counts are provider-billed — only chat tokens are priced client-side.
 * When a model has no known price, cost is undefined and the UI shows "—" (never $0).
 *
 * An AbortController is created per-operation; "Abort" in the top bar calls
 * abort() and resets phase to "idle". Object URLs are tracked and revoked on
 * unmount. Mic detection runs once on mount and gracefully degrades when
 * navigator.mediaDevices is absent (jsdom, some browsers).
 *
 * Backward-compatible with all frozen test IDs:
 *   test_stt_upload_shows_transcript  — aria-label="Audio file" + Transcribe
 *   test_tts_plays_audio              — "Text to speak" + Speak + audio-player
 *   test_upstream_error_shows_error_state — role="alert" on STT 502
 *   test_bff_forwards_binary_unmangled, test_bff_json_path_forwards_as_string
 *   test_voice_nav_role_open          — NAV_ITEMS (unchanged)
 *   test_model_fields_suggest_catalog_audio_models — datalist ids
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { VoiceTopBar } from "./VoiceTopBar";
import { VoiceThread } from "./VoiceThread";
import { VoiceComposer } from "./VoiceComposer";
import { VoiceInspector } from "./VoiceInspector";
import type { Phase, VoiceTurn, VoiceRecorder } from "./voice-types";
import { bffGet } from "@/lib/bff-client";
import { computeTurnCost } from "@/lib/voice-cost";
import type { ModelsData } from "@/components/models/ModelCatalogTable";

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

// ── Default media seam implementations ────────────────────────────────────────

/** Default mic stream request — uses real browser getUserMedia. */
function defaultRequestMicStream(): Promise<MediaStream> {
  return navigator.mediaDevices.getUserMedia({ audio: true });
}

/**
 * Default recorder factory — wraps MediaRecorder.
 * Collects chunks into a Blob, resolves on "stop" event.
 */
function defaultCreateRecorder(stream: MediaStream): VoiceRecorder {
  const chunks: Blob[] = [];
  const mr = new MediaRecorder(stream);
  mr.ondataavailable = (e) => {
    if (e.data.size > 0) chunks.push(e.data);
  };
  let resolveFn!: (blob: Blob) => void;
  const stopPromise = new Promise<Blob>((resolve) => {
    resolveFn = resolve;
  });
  mr.onstop = () => {
    const blob = new Blob(chunks, { type: mr.mimeType || "audio/webm" });
    resolveFn(blob);
  };
  return {
    start: () => { mr.start(); },
    stop: () => { mr.stop(); return stopPromise; },
  };
}

// ── Component ─────────────────────────────────────────────────────────────────

export interface VoicePlaygroundProps {
  /**
   * Injectable mic-stream factory — replaces navigator.mediaDevices.getUserMedia.
   * When provided, the mic button becomes active even in jsdom (no real device needed).
   * Default: navigator.mediaDevices.getUserMedia({ audio: true })
   */
  requestMicStream?: () => Promise<MediaStream>;
  /**
   * Injectable recorder factory — replaces MediaRecorder.
   * Tests inject a stub that synchronously resolves stop() with a Blob.
   * Default: wraps MediaRecorder with ondataavailable + onstop.
   */
  createRecorder?: (stream: MediaStream) => VoiceRecorder;
}

export function VoicePlayground({
  requestMicStream,
  createRecorder,
}: VoicePlaygroundProps = {}) {
  const [turns, setTurns] = useState<VoiceTurn[]>([]);
  const [sttModel, setSttModel] = useState("whisper-1");
  const [chatModel, setChatModel] = useState("openai/gpt-4o");
  const [ttsVoice, setTtsVoice] = useState("alloy");
  const [ttsModel, setTtsModel] = useState("tts-1");
  const [ttsFormat, setTtsFormat] = useState("mp3");
  const [sessionTokens, setSessionTokens] = useState(0);
  const [phase, setPhase] = useState<Phase>("idle");
  const [errorTitle, setErrorTitle] = useState<string | null>(null);
  const [isRecording, setIsRecording] = useState(false);

  // Mic available: true when an injected seam is provided OR the browser has
  // navigator.mediaDevices.getUserMedia. Stays false in jsdom without injection.
  const [micAvailable] = useState(
    () =>
      requestMicStream != null ||
      (typeof window !== "undefined" &&
        !!(
          typeof navigator !== "undefined" &&
          navigator.mediaDevices &&
          typeof navigator.mediaDevices.getUserMedia === "function"
        )),
  );

  // Per-model pricing from /admin/catalog/models — same map structure as ChatWorkspace.
  // Honest degrade: stays empty when the fetch fails or the catalog has no prices.
  const [priceMap, setPriceMap] = useState<Map<string, { p: number; c: number }>>(new Map());

  useEffect(() => {
    let alive = true;
    bffGet<ModelsData>("/admin/catalog/models")
      .then((d) => {
        if (!alive) return;
        const m = new Map<string, { p: number; c: number }>();
        for (const e of d.data ?? []) {
          if (
            typeof e.prompt_per_token === "number" &&
            typeof e.completion_per_token === "number"
          ) {
            m.set(e.id, { p: e.prompt_per_token, c: e.completion_per_token });
          }
        }
        setPriceMap(m);
      })
      .catch(() => {
        /* honest degrade — turns show tokens but no dollar cost */
      });
    return () => {
      alive = false;
    };
  }, []);

  // Session cost: sum of all per-turn costs. Returns null (honest-absent) until
  // at least one priced turn exists. Never fabricates $0 for an unpriced model.
  const sessionCost = useMemo<number | null>(() => {
    let total = 0;
    let hasAny = false;
    for (const turn of turns) {
      const c = turn.meta.cost;
      if (c != null && Number.isFinite(c)) {
        total += c;
        hasAny = true;
      }
    }
    return hasAny ? total : null;
  }, [turns]);

  // ID of the most-recent turn that has TTS audio — used to set autoPlay on that
  // turn's <audio> element only. Prevents re-triggering previously played turns.
  const latestAudioTurnId = useMemo(() => {
    for (let i = turns.length - 1; i >= 0; i--) {
      if (turns[i].audioSrc !== null) return turns[i].id;
    }
    return undefined;
  }, [turns]);

  const abortRef = useRef<AbortController | null>(null);
  const objectUrlsRef = useRef<string[]>([]);
  const activeStreamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<VoiceRecorder | null>(null);

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

  // ── STT-only path ────────────────────────────────────────────────────────────

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

  // ── TTS-only path ────────────────────────────────────────────────────────────

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

  // ── Full voice turn loop (text input → chat → TTS) ────────────────────────

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
      const cost = computeTurnCost(
        chatModel,
        usage.prompt_tokens,
        usage.completion_tokens,
        priceMap,
      );
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
          cost,
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

  // ── Mic path (hold-to-record → STT → chat → TTS) ─────────────────────────

  /**
   * Called on pointerDown on the mic button. Requests the mic stream (once),
   * creates a recorder via the injectable factory, and starts capture.
   * Sets phase to "recording" on success.
   * On permission-denied / insecure-context: stays at "Mic not available" pill;
   * the file-upload path remains usable. No retry storm.
   */
  async function handleRecordStart(): Promise<void> {
    if (recorderRef.current) return; // idempotent — already recording
    const reqStream = requestMicStream ?? defaultRequestMicStream;
    const makeRecorder = createRecorder ?? defaultCreateRecorder;
    try {
      const stream = await reqStream();
      activeStreamRef.current = stream;
      const rec = makeRecorder(stream);
      recorderRef.current = rec;
      rec.start();
      setIsRecording(true);
      setPhase("recording");
    } catch {
      // Permission denied, insecure context, or no device — degrade gracefully.
      setErrorTitle("Microphone access denied");
      setPhase("error");
    }
  }

  /**
   * Called on pointerUp / pointerLeave / pointerCancel on the mic button.
   * Idempotent: sets recorderRef to null at the top so concurrent calls are no-ops.
   * Empty blob (< 1 byte) → return to idle without sending a request.
   */
  async function handleRecordStop(): Promise<void> {
    const rec = recorderRef.current;
    if (!rec) return; // already stopped or never started
    recorderRef.current = null; // guard against double-fire (pointerUp + pointerLeave)
    setIsRecording(false);

    try {
      const blob = await rec.stop();

      // Stop all mic tracks to release the hardware indicator.
      // Use optional chaining on getTracks to guard injected fake streams in tests.
      activeStreamRef.current?.getTracks?.().forEach((t) => t.stop());
      activeStreamRef.current = null;

      if (blob.size === 0) {
        // Empty utterance — silently return to idle; no network request.
        setPhase("idle");
        return;
      }

      await handleMicTurn(blob);
    } catch {
      setErrorTitle("Recording failed");
      setPhase("error");
    }
  }

  /**
   * Feeds a recorded Blob through the full STT → chat → TTS pipeline.
   * The captured Blob takes the place of the uploaded file in the STT step;
   * the resulting transcript is then fed into the same chat + TTS flow as
   * handleVoiceTurn. This keeps all network logic co-located in one component.
   */
  async function handleMicTurn(blob: Blob): Promise<void> {
    const t0 = Date.now();
    setPhase("transcribing");
    setErrorTitle(null);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      // Step 1: STT — send the recorded blob as a File
      const form = new FormData();
      form.append(
        "file",
        new File([blob], "recording.webm", { type: blob.type || "audio/webm" }),
      );
      form.append("model", sttModel);

      const sttRes = await fetch(`${bffBase()}/api/gw/v1/audio/transcriptions`, {
        method: "POST",
        body: form,
        signal: ctrl.signal,
      });

      if (!sttRes.ok) {
        setErrorTitle(await extractErrorTitle(sttRes));
        setPhase("error");
        return;
      }

      const { text: transcript } = (await sttRes.json()) as { text: string };
      if (!transcript.trim()) {
        // Empty transcription — nothing to send; return to idle gracefully.
        setPhase("idle");
        return;
      }

      // Step 2: chat completion
      setPhase("thinking");
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

      // Step 3: TTS synthesis
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

      const audioBlob = await ttsRes.blob();
      const url = URL.createObjectURL(audioBlob);
      trackObjectUrl(url);

      const latencyMs = Date.now() - t0;
      const cost = computeTurnCost(
        chatModel,
        usage.prompt_tokens,
        usage.completion_tokens,
        priceMap,
      );
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
          cost,
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

  // ── Abort ────────────────────────────────────────────────────────────────────

  function handleAbort(): void {
    abortRef.current?.abort();
    // Also cancel any active recording — guard getTracks for injected fake streams.
    if (recorderRef.current) {
      recorderRef.current = null;
      setIsRecording(false);
      activeStreamRef.current?.getTracks?.().forEach((t) => t.stop());
      activeStreamRef.current = null;
    }
    setPhase("idle");
    setErrorTitle(null);
  }

  // ── Render ───────────────────────────────────────────────────────────────────

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

        <VoiceThread turns={turns} latestTurnId={latestAudioTurnId} />

        <VoiceComposer
          phase={phase}
          errorTitle={errorTitle}
          micAvailable={micAvailable}
          isRecording={isRecording}
          onTranscribe={handleTranscribe}
          onSpeak={handleSpeak}
          onVoiceTurn={handleVoiceTurn}
          onRecordStart={() => { void handleRecordStart(); }}
          onRecordStop={() => { void handleRecordStop(); }}
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
