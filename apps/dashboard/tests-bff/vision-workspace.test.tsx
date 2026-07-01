/**
 * tests-bff/vision-workspace.test.tsx — RED suite for v46 vision workspace.
 *
 * Pins the frozen contract:
 *   - VisionWorkspace (new component): file pick / prompt / model select / ask
 *   - lib/vision (new BFF client): askVision
 *
 * Mirrors artifacts-workspace.test.tsx for MSW setup + FileReader stub pattern.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";

// ── imports under test ────────────────────────────────────────────────────────
import { VisionWorkspace } from "@/components/vision/VisionWorkspace";
import { askVision } from "@/lib/vision";

// ── URL constants ─────────────────────────────────────────────────────────────
const URL_MODELS = "http://localhost:3000/api/gw/admin/catalog/models";
const URL_CHAT = "http://localhost:3000/api/gw/v1/chat/completions";

// ── helpers ───────────────────────────────────────────────────────────────────
function stubFileReader(dataUrl: string) {
  vi.stubGlobal(
    "FileReader",
    class {
      onload: ((ev: ProgressEvent) => void) | null = null;
      onerror: ((ev: ProgressEvent) => void) | null = null;
      readAsDataURL(_file: File) {
        setTimeout(() => {
          if (this.onload) {
            this.onload({
              target: { result: dataUrl },
            } as unknown as ProgressEvent);
          }
        }, 0);
      }
    },
  );
}

// ── lib/vision unit tests ─────────────────────────────────────────────────────
describe("lib/vision — askVision BFF client", () => {
  it("askVision_posts_image_url_part_for_image_kind", async () => {
    let captured: unknown;
    server.use(
      http.post(URL_CHAT, async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({
          choices: [{ message: { content: "A scenic mountain view." } }],
        });
      }),
    );

    const result = await askVision({
      model: "gemini-2.5-flash",
      prompt: "Describe this image",
      dataUrl: "data:image/png;base64,abc123",
      mediaType: "image",
    });

    expect(result).toBe("A scenic mountain view.");
    const body = captured as {
      model: string;
      stream: boolean;
      messages: Array<{ role: string; content: Array<{ type: string; text?: string; image_url?: { url: string }; video_url?: { url: string } }> }>;
    };
    expect(body.model).toBe("gemini-2.5-flash");
    expect(body.stream).toBe(false);
    expect(body.messages).toHaveLength(1);
    expect(body.messages[0].role).toBe("user");
    const parts = body.messages[0].content;
    expect(parts).toHaveLength(2);
    expect(parts[0]).toEqual({ type: "text", text: "Describe this image" });
    expect(parts[1]).toEqual({
      type: "image_url",
      image_url: { url: "data:image/png;base64,abc123" },
    });
  });

  it("askVision_posts_video_url_part_for_video_kind", async () => {
    let captured: unknown;
    server.use(
      http.post(URL_CHAT, async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({
          choices: [{ message: { content: "A cat video." } }],
        });
      }),
    );

    await askVision({
      model: "gemini-2.5-flash",
      prompt: "What is in this video?",
      dataUrl: "data:video/mp4;base64,xyz",
      mediaType: "video",
    });

    const body = captured as {
      messages: Array<{ content: Array<{ type: string; video_url?: { url: string } }> }>;
    };
    const parts = body.messages[0].content;
    expect(parts[1]).toEqual({
      type: "video_url",
      video_url: { url: "data:video/mp4;base64,xyz" },
    });
  });

  it("askVision_returns_empty_string_on_missing_content", async () => {
    server.use(
      http.post(URL_CHAT, () => HttpResponse.json({ choices: [] })),
    );
    const result = await askVision({
      model: "gemini-2.5-flash",
      prompt: "test",
      dataUrl: "data:image/png;base64,x",
      mediaType: "image",
    });
    expect(result).toBe("");
  });
});

// ── VisionWorkspace component tests ───────────────────────────────────────────
describe("VisionWorkspace", () => {
  beforeEach(() => {
    // Default: one gemini model available
    server.use(
      http.get(URL_MODELS, () =>
        HttpResponse.json({
          object: "list",
          data: [{ id: "gemini-2.5-flash" }, { id: "openai/gpt-4o" }],
        }),
      ),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  // ── test_ask_about_image ────────────────────────────────────────────────────
  it("test_ask_about_image", async () => {
    let captured: unknown;
    server.use(
      http.post(URL_CHAT, async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({
          choices: [{ message: { content: "A beautiful sunset over mountains." } }],
        });
      }),
    );

    stubFileReader("data:image/jpeg;base64,/9j/4AAQ");

    const user = userEvent.setup();
    render(<VisionWorkspace />);

    // Wait for the gemini model to appear in the select
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: /model/i })).toBeInTheDocument(),
    );

    // The file input
    const fileInput = screen.getByTestId("vision-file-input");
    const imageFile = new File(["img"], "photo.jpg", { type: "image/jpeg" });
    await user.upload(fileInput, imageFile);

    // Type a prompt
    const textarea = screen.getByRole("textbox", { name: /prompt/i });
    await user.type(textarea, "Describe this image");

    // Ask button enabled
    const askBtn = screen.getByRole("button", { name: /ask/i });
    await waitFor(() => expect(askBtn).not.toBeDisabled());

    // Click Ask
    await user.click(askBtn);

    // Assert POST body has correct structure
    await waitFor(() => {
      const body = captured as {
        model: string;
        stream: boolean;
        messages: Array<{ role: string; content: Array<{ type: string; text?: string; image_url?: { url: string } }> }>;
      };
      expect(body.model).toBe("gemini-2.5-flash");
      expect(body.stream).toBe(false);
      const parts = body.messages[0].content;
      expect(parts[0]).toEqual({ type: "text", text: "Describe this image" });
      expect(parts[1]).toEqual({
        type: "image_url",
        image_url: { url: "data:image/jpeg;base64,/9j/4AAQ" },
      });
    });

    // Answer renders
    await screen.findByText("A beautiful sunset over mountains.");
  });

  // ── test_ask_about_video ────────────────────────────────────────────────────
  it("test_ask_about_video", async () => {
    let captured: unknown;
    server.use(
      http.post(URL_CHAT, async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({
          choices: [{ message: { content: "A cat playing with yarn." } }],
        });
      }),
    );

    stubFileReader("data:video/mp4;base64,AAAAFGZ0");

    const user = userEvent.setup();
    render(<VisionWorkspace />);

    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: /model/i })).toBeInTheDocument(),
    );

    const fileInput = screen.getByTestId("vision-file-input");
    const videoFile = new File(["vid"], "clip.mp4", { type: "video/mp4" });
    await user.upload(fileInput, videoFile);

    const textarea = screen.getByRole("textbox", { name: /prompt/i });
    await user.type(textarea, "What is in this video?");

    const askBtn = screen.getByRole("button", { name: /ask/i });
    await waitFor(() => expect(askBtn).not.toBeDisabled());
    await user.click(askBtn);

    await waitFor(() => {
      const body = captured as {
        messages: Array<{ content: Array<{ type: string; video_url?: { url: string } }> }>;
      };
      const parts = body.messages[0].content;
      expect(parts[1]).toEqual({
        type: "video_url",
        video_url: { url: "data:video/mp4;base64,AAAAFGZ0" },
      });
    });

    await screen.findByText("A cat playing with yarn.");
  });

  // ── test_ask_disabled_until_ready ───────────────────────────────────────────
  it("test_ask_disabled_until_ready", async () => {
    let postCalled = false;
    server.use(
      http.post(URL_CHAT, () => {
        postCalled = true;
        return HttpResponse.json({
          choices: [{ message: { content: "answer" } }],
        });
      }),
    );

    const user = userEvent.setup();
    render(<VisionWorkspace />);

    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: /model/i })).toBeInTheDocument(),
    );

    const askBtn = screen.getByRole("button", { name: /ask/i });

    // Initially disabled — no file, no prompt
    expect(askBtn).toBeDisabled();

    // Clicking does nothing
    await user.click(askBtn);
    expect(postCalled).toBe(false);

    // Still disabled after only adding prompt
    const textarea = screen.getByRole("textbox", { name: /prompt/i });
    await user.type(textarea, "hello");
    expect(askBtn).toBeDisabled();

    // Still disabled after only adding file (no prompt cleared)
    stubFileReader("data:image/png;base64,abc");
    const fileInput = screen.getByTestId("vision-file-input");
    const file = new File(["img"], "test.png", { type: "image/png" });

    // Reset textarea first to re-test file-only state
    await user.clear(textarea);
    await user.upload(fileInput, file);
    // need to wait for FileReader
    await waitFor(() => {
      // file loaded state means button might or might not be enabled depending on prompt
    });
    // With file but no prompt → still disabled
    expect(askBtn).toBeDisabled();
    expect(postCalled).toBe(false);
  });

  // ── test_failure_nonblocking ────────────────────────────────────────────────
  it("test_failure_nonblocking", async () => {
    server.use(
      http.post(URL_CHAT, () =>
        HttpResponse.json(
          { title: "Request Entity Too Large", status: 413 },
          { status: 413 },
        ),
      ),
    );

    stubFileReader("data:image/png;base64,abc");

    const user = userEvent.setup();
    render(<VisionWorkspace />);

    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: /model/i })).toBeInTheDocument(),
    );

    const fileInput = screen.getByTestId("vision-file-input");
    const file = new File(["img"], "huge.png", { type: "image/png" });
    await user.upload(fileInput, file);

    const textarea = screen.getByRole("textbox", { name: /prompt/i });
    await user.type(textarea, "Describe this");

    const askBtn = screen.getByRole("button", { name: /ask/i });
    await waitFor(() => expect(askBtn).not.toBeDisabled());
    await user.click(askBtn);

    // role="alert" with 413-specific message
    const alert = await screen.findByRole("alert");
    expect(alert).toBeInTheDocument();
    expect(alert).toHaveTextContent(/media too large/i);

    // No crash — workspace is still mounted
    expect(screen.getByTestId("vision-file-input")).toBeInTheDocument();
  });

  // ── test_no_gemini_model ────────────────────────────────────────────────────
  it("test_no_gemini_model", async () => {
    // Override: no gemini models, only non-gemini
    server.use(
      http.get(URL_MODELS, () =>
        HttpResponse.json({
          object: "list",
          data: [{ id: "gpt-4o" }, { id: "claude-3-5-sonnet" }],
        }),
      ),
    );

    let postCalled = false;
    server.use(
      http.post(URL_CHAT, () => {
        postCalled = true;
        return HttpResponse.json({ choices: [] });
      }),
    );

    render(<VisionWorkspace />);

    // Should show "No Gemini model available"
    await screen.findByText(/no gemini model available/i);

    // Ask flow is unavailable — button should be disabled or absent
    const askBtn = screen.queryByRole("button", { name: /ask/i });
    if (askBtn) {
      expect(askBtn).toBeDisabled();
    }

    // Clicking would not trigger POST
    if (askBtn) {
      await userEvent.click(askBtn);
    }
    expect(postCalled).toBe(false);
  });
});

// ── Console-grade surface tests ───────────────────────────────────────────────
describe("VisionWorkspace — Console-grade surface", () => {
  beforeEach(() => {
    server.use(
      http.get(URL_MODELS, () =>
        HttpResponse.json({
          object: "list",
          data: [{ id: "gemini-2.5-flash" }, { id: "openai/gpt-4o" }],
        }),
      ),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  // test_multi_turn_thread — ask twice on same media; thread accumulates turns
  it("test_multi_turn_thread", async () => {
    let callCount = 0;
    server.use(
      http.post(URL_CHAT, () => {
        callCount++;
        return HttpResponse.json({
          choices: [
            { message: { content: callCount === 1 ? "First answer." : "Second answer." } },
          ],
          usage: { total_tokens: 10 },
        });
      }),
    );

    stubFileReader("data:image/png;base64,aGVsbG8=");
    const user = userEvent.setup();
    render(<VisionWorkspace />);

    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: /model/i })).toBeInTheDocument(),
    );

    const fileInput = screen.getByTestId("vision-file-input");
    await user.upload(fileInput, new File(["img"], "photo.jpg", { type: "image/jpeg" }));

    const textarea = screen.getByRole("textbox", { name: /prompt/i });
    await user.type(textarea, "First question");

    const askBtn = screen.getByRole("button", { name: /ask/i });
    await waitFor(() => expect(askBtn).not.toBeDisabled());
    await user.click(askBtn);

    // First answer appears in thread
    await screen.findByText("First answer.");

    // Thread container (role="log") must exist
    expect(screen.getByRole("log")).toBeInTheDocument();

    // Type second follow-up
    await user.type(textarea, "Second question");
    await waitFor(() => expect(askBtn).not.toBeDisabled());
    await user.click(askBtn);

    // Second answer also appears in thread
    await screen.findByText("Second answer.");

    // Both answers still in the DOM (thread preserved)
    expect(screen.getByText("First answer.")).toBeInTheDocument();
    expect(screen.getByText("Second answer.")).toBeInTheDocument();
    expect(callCount).toBe(2);
  });

  // test_media_preview_image — after image upload, <img alt="Media preview"> renders
  it("test_media_preview_image", async () => {
    stubFileReader("data:image/png;base64,iVBORw0KGgo=");
    const user = userEvent.setup();
    render(<VisionWorkspace />);

    const fileInput = screen.getByTestId("vision-file-input");
    await user.upload(fileInput, new File(["img"], "photo.png", { type: "image/png" }));

    await waitFor(() => {
      const img = screen.getByRole("img", { name: /media preview/i });
      expect(img).toBeInTheDocument();
      expect(img).toHaveAttribute("src", "data:image/png;base64,iVBORw0KGgo=");
    });
  });

  // test_media_preview_video — after video upload, <video aria-label="Video preview"> renders
  it("test_media_preview_video", async () => {
    stubFileReader("data:video/mp4;base64,AAAAFGZ0");
    const user = userEvent.setup();
    render(<VisionWorkspace />);

    const fileInput = screen.getByTestId("vision-file-input");
    await user.upload(fileInput, new File(["vid"], "clip.mp4", { type: "video/mp4" }));

    await waitFor(() => {
      const video = screen.getByLabelText(/video preview/i);
      expect(video).toBeInTheDocument();
      expect(video.tagName).toBe("VIDEO");
    });
  });

  // test_inspector_shows_token_count — after answer with usage, inspector shows token count
  it("test_inspector_shows_token_count", async () => {
    server.use(
      http.post(URL_CHAT, () =>
        HttpResponse.json({
          choices: [{ message: { content: "Answer with tokens." } }],
          usage: { total_tokens: 42, prompt_tokens: 20, completion_tokens: 22 },
        }),
      ),
    );

    stubFileReader("data:image/png;base64,abc");
    const user = userEvent.setup();
    render(<VisionWorkspace />);

    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: /model/i })).toBeInTheDocument(),
    );

    const fileInput = screen.getByTestId("vision-file-input");
    await user.upload(fileInput, new File(["img"], "photo.png", { type: "image/png" }));

    const textarea = screen.getByRole("textbox", { name: /prompt/i });
    await user.type(textarea, "What is this?");

    const askBtn = screen.getByRole("button", { name: /ask/i });
    await waitFor(() => expect(askBtn).not.toBeDisabled());
    await user.click(askBtn);

    await screen.findByText("Answer with tokens.");

    await waitFor(() => {
      const tokensEl = screen.getByTestId("inspector-tokens");
      expect(tokensEl).toHaveTextContent("42");
    });
  });
});
