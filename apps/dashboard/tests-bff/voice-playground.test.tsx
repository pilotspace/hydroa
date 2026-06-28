/**
 * tests-bff/voice-playground.test.tsx — RED suite for v40 voice-playground.
 *
 * Pins the frozen contract:
 *   - BFF forwards binary multipart bodies as raw bytes (not UTF-8 text)
 *   - VoicePlayground STT panel uploads audio and renders transcript
 *   - VoicePlayground TTS panel synthesises speech and mounts <audio>
 *   - Upstream error renders ErrorState; no transcript/audio mounts
 *   - Voice nav entry visible to member (role-open)
 *
 * RED before Build: VoicePlayground + route.ts binary-branch don't exist.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { NextRequest } from "next/server";
import { VALID_SESSION_JWT } from "./mocks/handlers";

// ── RED: these modules do not exist until Build ─────────────────────────────
import { VoicePlayground } from "@/components/voice/VoicePlayground";
import { POST } from "@/app/api/gw/[...path]/route";
import { NAV_ITEMS } from "@/components/ui/app-shell";

// ── helpers ──────────────────────────────────────────────────────────────────
const APP = "http://localhost:3000";

function ctx(path: string[]) {
  return { params: Promise.resolve({ path }) };
}

// ════════════════════════════════════════════════════════════════════════════
describe("VoicePlayground — STT panel", () => {
  it("test_stt_upload_shows_transcript", async () => {
    let capturedContentType: string | null = null;

    server.use(
      http.post(`${APP}/api/gw/v1/audio/transcriptions`, ({ request }) => {
        capturedContentType = request.headers.get("content-type");
        return HttpResponse.json({ text: "hello world" });
      }),
    );

    const user = userEvent.setup();
    render(<VoicePlayground />);

    // Attach a file to the file input
    const fileInput = screen.getByLabelText(/audio file/i);
    const audioBytes = new Uint8Array([0x49, 0x44, 0x33]); // fake MP3 bytes
    const file = new File([audioBytes], "a.mp3", { type: "audio/mpeg" });
    fireEvent.change(fileInput, { target: { files: [file] } });

    // Click Transcribe
    await user.click(screen.getByRole("button", { name: /transcribe/i }));

    // The transcript text should appear
    expect(await screen.findByText("hello world")).toBeInTheDocument();

    // The upstream request must be multipart/form-data (NOT application/json)
    expect(capturedContentType).toBeTruthy();
    expect(capturedContentType!.startsWith("multipart/form-data")).toBe(true);
  });
});

describe("VoicePlayground — TTS panel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("test_tts_plays_audio", async () => {
    // Stub URL.createObjectURL / revokeObjectURL as spies — do NOT replace the
    // entire URL global (that would break NextRequest URL parsing).
    const createObjectURLSpy = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue("blob:mock");
    vi.spyOn(URL, "revokeObjectURL").mockReturnValue(undefined);

    let capturedBody: Record<string, unknown> | null = null;

    server.use(
      http.post(`${APP}/api/gw/v1/audio/speech`, async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        // Respond with fake audio bytes
        const fakeAudio = new Uint8Array([0x49, 0x44, 0x33]);
        return new HttpResponse(fakeAudio, {
          headers: { "Content-Type": "audio/mpeg" },
        });
      }),
    );

    const user = userEvent.setup();
    render(<VoicePlayground />);

    // Type text into the TTS textarea
    const textInput = screen.getByLabelText(/text to speak/i);
    await user.type(textInput, "Hello voice");

    // Click Speak
    await user.click(screen.getByRole("button", { name: /speak/i }));

    // An <audio> element should mount with src="blob:mock"
    // jsdom does not assign an ARIA role to <audio> — query by data-testid.
    await waitFor(() => {
      expect(createObjectURLSpy).toHaveBeenCalled();
    });
    const audioEl = await screen.findByTestId<HTMLAudioElement>("audio-player");
    expect(audioEl).toBeInTheDocument();
    expect(audioEl.getAttribute("src")).toBe("blob:mock");

    // Captured body must have input + voice
    expect(capturedBody).not.toBeNull();
    expect(capturedBody!.input).toBe("Hello voice");
    expect(typeof capturedBody!.voice).toBe("string");
  });
});

describe("VoicePlayground — error state", () => {
  it("test_upstream_error_shows_error_state", async () => {
    server.use(
      http.post(`${APP}/api/gw/v1/audio/transcriptions`, () =>
        HttpResponse.json(
          { title: "Upstream unavailable", status: 502 },
          { status: 502 },
        ),
      ),
    );

    const user = userEvent.setup();
    render(<VoicePlayground />);

    // Attach a file so the submit isn't a no-op
    const fileInput = screen.getByLabelText(/audio file/i);
    const file = new File([new Uint8Array([0])], "a.mp3", { type: "audio/mpeg" });
    fireEvent.change(fileInput, { target: { files: [file] } });

    await user.click(screen.getByRole("button", { name: /transcribe/i }));

    // ErrorState renders role="alert" with the error title
    const alert = await screen.findByRole("alert");
    expect(alert).toBeInTheDocument();
    expect(alert.textContent).toMatch(/upstream unavailable/i);

    // No transcript text, no audio element
    expect(screen.queryByTestId("audio-player")).not.toBeInTheDocument();
    expect(screen.queryByText("hello world")).not.toBeInTheDocument();
  });
});

describe("BFF route.ts — binary body forwarding", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("test_bff_forwards_binary_unmangled", async () => {
    // Build a multipart/form-data Request (content-type WITHOUT application/json)
    // and assert the upstream fetch receives an ArrayBuffer, not a string.
    let capturedBody: BodyInit | null | undefined;

    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (_url: RequestInfo | URL, init?: RequestInit) => {
        capturedBody = init?.body;
        return new Response(JSON.stringify({ text: "ok" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      },
    );

    // A multipart body — simulate by setting content-type and providing ArrayBuffer
    const audioBytes = new Uint8Array([0x49, 0x44, 0x33]);
    const boundary = "boundary123";
    const multipartRequest = new NextRequest(
      "http://localhost:3000/api/gw/v1/audio/transcriptions",
      {
        method: "POST",
        headers: {
          Cookie: `ai_proxy_session=${VALID_SESSION_JWT}`,
          // content-type without application/json → triggers binary branch
          "Content-Type": `multipart/form-data; boundary=${boundary}`,
        },
        body: audioBytes.buffer,
      },
    );

    await POST(multipartRequest, ctx(["v1", "audio", "transcriptions"]));

    // Body forwarded as ArrayBuffer (not a string)
    expect(capturedBody).toBeInstanceOf(ArrayBuffer);
    expect(typeof capturedBody).not.toBe("string");
  });

  it("test_bff_json_path_forwards_as_string", async () => {
    // A JSON body + stream:true must still be forwarded as a string (no regression)
    let capturedBody: BodyInit | null | undefined;

    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (_url: RequestInfo | URL, init?: RequestInit) => {
        capturedBody = init?.body;
        return new Response("data: [DONE]\n\n", {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        });
      },
    );

    const jsonRequest = new NextRequest(
      "http://localhost:3000/api/gw/v1/chat/completions",
      {
        method: "POST",
        headers: {
          Cookie: `ai_proxy_session=${VALID_SESSION_JWT}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ model: "gpt-4o", messages: [], stream: true }),
      },
    );

    await POST(jsonRequest, ctx(["v1", "chat", "completions"]));

    // Body forwarded as a string (JSON path unchanged)
    expect(typeof capturedBody).toBe("string");
  });
});

describe("nav entry — Voice", () => {
  it("test_voice_nav_role_open", () => {
    const voiceItem = NAV_ITEMS.find((i) => i.href === "/app/voice");
    expect(voiceItem).toBeDefined();
    expect(voiceItem?.label).toBe("Voice");
    // role-open: no minRole restriction
    expect(voiceItem?.minRole).toBeUndefined();
  });
});

describe("VoicePlayground — model autocomplete", () => {
  it("test_model_fields_suggest_catalog_audio_models", async () => {
    // Both panels read /admin/catalog/models for datalist suggestions; the fields stay
    // free-text (the defaults whisper-1/tts-1 may not be in the catalog).
    server.use(
      http.get(`${APP}/api/gw/admin/catalog/models`, () =>
        HttpResponse.json({
          object: "list",
          data: [
            { id: "openai/whisper-1" },
            { id: "openai/tts-1-hd" },
            { id: "openai/gpt-4o" },
          ],
        }),
      ),
    );

    render(<VoicePlayground />);

    // STT field suggests transcription models, not chat models.
    await waitFor(() => {
      const stt = Array.from(
        document.querySelectorAll("#stt-model-options option"),
      ).map((o) => (o as HTMLOptionElement).value);
      expect(stt).toContain("openai/whisper-1");
      expect(stt).not.toContain("openai/gpt-4o");
    });

    // TTS field suggests speech models, not chat models.
    const tts = Array.from(
      document.querySelectorAll("#tts-model-options option"),
    ).map((o) => (o as HTMLOptionElement).value);
    expect(tts).toContain("openai/tts-1-hd");
    expect(tts).not.toContain("openai/gpt-4o");
  });
});
