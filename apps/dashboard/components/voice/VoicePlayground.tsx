"use client";

/**
 * components/voice/VoicePlayground.tsx — v40 voice-playground.
 *
 * Two panels:
 *   STT: upload an audio file → POST /api/gw/v1/audio/transcriptions (multipart,
 *        no explicit Content-Type so the browser sets the boundary) → render text.
 *   TTS: type text → POST /api/gw/v1/audio/speech (JSON) → mount <audio>.
 *
 * All network calls go to /api/gw/... (the BFF) — never the gateway directly.
 * No new dependencies; uses the v13/v23 ui primitives.
 */

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Empty, ErrorState, Loading } from "@/components/ui/states";

/**
 * Absolute base for BFF fetches. In Node/jsdom (vitest), undici rejects
 * relative URLs — use the same appBase() pattern as bff-client.ts.
 * In the real browser, empty string lets the browser resolve from window.location.
 */
function bffBase(): string {
  if (typeof process !== "undefined" && process.versions?.node) {
    return (
      process.env.NEXT_PUBLIC_APP_URL ??
      "http://localhost:3000"
    );
  }
  return "";
}

// ── types ────────────────────────────────────────────────────────────────────

type PanelStatus = "idle" | "loading" | "error" | "success";

interface ProblemJson {
  title?: string;
  status?: number;
}

// ── STT Panel ────────────────────────────────────────────────────────────────

function SttPanel() {
  const [file, setFile] = useState<File | null>(null);
  const [model, setModel] = useState("whisper-1");
  const [status, setStatus] = useState<PanelStatus>("idle");
  const [transcript, setTranscript] = useState<string | null>(null);
  const [errorTitle, setErrorTitle] = useState<string>("");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!file) return; // guard: no file → no-op

    setStatus("loading");
    setTranscript(null);
    setErrorTitle("");

    const form = new FormData();
    form.append("file", file);
    form.append("model", model);

    try {
      // DO NOT set Content-Type — the browser sets the multipart boundary.
      const res = await fetch(`${bffBase()}/api/gw/v1/audio/transcriptions`, {
        method: "POST",
        body: form,
      });

      if (!res.ok) {
        let title = `HTTP ${res.status}`;
        try {
          const problem = (await res.json()) as ProblemJson;
          if (problem.title) title = problem.title;
        } catch {
          // ignore json parse failure
        }
        setErrorTitle(title);
        setStatus("error");
        return;
      }

      const data = (await res.json()) as { text: string };
      setTranscript(data.text);
      setStatus("success");
    } catch {
      setErrorTitle("Request failed");
      setStatus("error");
    }
  }

  return (
    <section
      aria-labelledby="stt-heading"
      className="flex flex-col gap-4 rounded-xl border border-border bg-background p-6"
    >
      <h2 id="stt-heading" className="text-base font-semibold text-foreground">
        Speech to Text
      </h2>

      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="stt-file" className="text-sm font-medium text-foreground">
            Audio file
          </label>
          <Input
            id="stt-file"
            type="file"
            accept="audio/*"
            aria-label="Audio file"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="cursor-pointer"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="stt-model" className="text-sm font-medium text-foreground">
            STT model
          </label>
          <Input
            id="stt-model"
            type="text"
            aria-label="STT model"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
        </div>

        <Button
          type="submit"
          disabled={status === "loading" || !file}
          aria-disabled={status === "loading" || !file}
        >
          {status === "loading" ? "Transcribing…" : "Transcribe"}
        </Button>
      </form>

      <div aria-label="Transcription result" aria-live="polite">
        {status === "idle" && (
          <Empty
            title="No transcript yet"
            description="Upload an audio file and click Transcribe."
          />
        )}
        {status === "loading" && <Loading label="Transcribing…" />}
        {status === "error" && (
          <ErrorState title={errorTitle} />
        )}
        {status === "success" && transcript !== null && (
          <div className="rounded-lg border border-border bg-muted/30 p-4">
            <p className="text-sm text-foreground">{transcript}</p>
          </div>
        )}
      </div>
    </section>
  );
}

// ── TTS Panel ────────────────────────────────────────────────────────────────

function TtsPanel() {
  const [text, setText] = useState("");
  const [voice, setVoice] = useState("alloy");
  const [model, setModel] = useState("tts-1");
  const [status, setStatus] = useState<PanelStatus>("idle");
  const [audioSrc, setAudioSrc] = useState<string | null>(null);
  const [errorTitle, setErrorTitle] = useState<string>("");
  const prevObjectUrlRef = useRef<string | null>(null);

  // Revoke the previous object URL when replaced or on unmount
  useEffect(() => {
    return () => {
      if (prevObjectUrlRef.current) {
        URL.revokeObjectURL(prevObjectUrlRef.current);
      }
    };
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed) return; // guard: empty text → no-op

    setStatus("loading");
    setErrorTitle("");

    // Revoke the previous object URL before creating a new one
    if (prevObjectUrlRef.current) {
      URL.revokeObjectURL(prevObjectUrlRef.current);
      prevObjectUrlRef.current = null;
    }
    setAudioSrc(null);

    try {
      const res = await fetch(`${bffBase()}/api/gw/v1/audio/speech`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model, input: trimmed, voice }),
      });

      if (!res.ok) {
        let title = `HTTP ${res.status}`;
        try {
          const problem = (await res.json()) as ProblemJson;
          if (problem.title) title = problem.title;
        } catch {
          // ignore
        }
        setErrorTitle(title);
        setStatus("error");
        return;
      }

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      prevObjectUrlRef.current = url;
      setAudioSrc(url);
      setStatus("success");
    } catch {
      setErrorTitle("Request failed");
      setStatus("error");
    }
  }

  return (
    <section
      aria-labelledby="tts-heading"
      className="flex flex-col gap-4 rounded-xl border border-border bg-background p-6"
    >
      <h2 id="tts-heading" className="text-base font-semibold text-foreground">
        Text to Speech
      </h2>

      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="tts-text" className="text-sm font-medium text-foreground">
            Text to speak
          </label>
          <Textarea
            id="tts-text"
            aria-label="Text to speak"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Enter text to synthesise…"
            rows={3}
          />
        </div>

        <div className="flex gap-3">
          <div className="flex flex-1 flex-col gap-1.5">
            <label htmlFor="tts-voice" className="text-sm font-medium text-foreground">
              Voice
            </label>
            <Input
              id="tts-voice"
              type="text"
              value={voice}
              onChange={(e) => setVoice(e.target.value)}
              placeholder="alloy"
            />
          </div>

          <div className="flex flex-1 flex-col gap-1.5">
            <label htmlFor="tts-model" className="text-sm font-medium text-foreground">
              TTS model
            </label>
            <Input
              id="tts-model"
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="tts-1"
            />
          </div>
        </div>

        <Button
          type="submit"
          disabled={status === "loading" || !text.trim()}
          aria-disabled={status === "loading" || !text.trim()}
        >
          {status === "loading" ? "Synthesising…" : "Speak"}
        </Button>
      </form>

      <div aria-label="Audio result" aria-live="polite">
        {status === "idle" && (
          <Empty
            title="No audio yet"
            description="Type some text and click Speak."
          />
        )}
        {status === "loading" && <Loading label="Synthesising…" />}
        {status === "error" && (
          <ErrorState title={errorTitle} />
        )}
        {status === "success" && audioSrc !== null && (
          // eslint-disable-next-line jsx-a11y/media-has-caption
          <audio controls src={audioSrc} className="w-full" data-testid="audio-player" />
        )}
      </div>
    </section>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function VoicePlayground() {
  return (
    <div className="flex h-full min-h-0 flex-col bg-muted/30">
      <header className="border-b border-border bg-background px-6 py-3">
        <h1 className="text-lg font-semibold text-foreground">Voice</h1>
      </header>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto grid max-w-4xl grid-cols-1 gap-6 lg:grid-cols-2">
          <SttPanel />
          <TtsPanel />
        </div>
      </div>
    </div>
  );
}
