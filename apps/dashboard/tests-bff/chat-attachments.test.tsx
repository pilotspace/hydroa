/**
 * tests-bff/chat-attachments.test.tsx — RED suite for chat-attachments.
 *
 * The composer gains an image-attach control (the previously-disabled Paperclip):
 * pick image(s) → staged thumbnail previews (removable) → on Send the user turn's
 * content becomes the OpenAI content-part array
 *   [{type:"text",text}, {type:"image_url",image_url:{url:"data:…;base64,…"}}]
 * sent pass-through to POST /api/gw/v1/chat/completions. Zero attachments ⇒ the
 * content stays a plain string (byte-identical off path). Oversized / unsupported /
 * over-the-count-cap picks are rejected with a visible message and never staged.
 *
 * RED before Build: today the attach control is disabled, content is always a
 * string, and there is no preview/validation path.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { ChatWorkspace } from "@/components/chat/ChatWorkspace";
import { MAX_IMAGE_BYTES } from "@/lib/chat/attachments";

const enc = new TextEncoder();
const URL_CHAT = "http://localhost:3000/api/gw/v1/chat/completions";
const sse = (o: unknown) => `data: ${JSON.stringify(o)}\n\n`;
const DONE = "data: [DONE]\n\n";

function sseResponse(parts: string[]): HttpResponse {
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      for (const p of parts) c.enqueue(enc.encode(p));
      c.close();
    },
  });
  return new HttpResponse(stream, { headers: { "Content-Type": "text/event-stream" } });
}

type ContentPart =
  | { type: "text"; text: string }
  | { type: "image_url"; image_url: { url: string } };
interface WireMessage {
  role: string;
  content: string | ContentPart[];
}
interface ChatBody {
  model: string;
  messages: WireMessage[];
  stream?: boolean;
  stream_options?: { include_usage?: boolean };
}

/** Capture every POST body; reply with a tiny content stream. */
function captureChat() {
  const calls: ChatBody[] = [];
  server.use(
    http.post(URL_CHAT, async ({ request }) => {
      calls.push((await request.json()) as ChatBody);
      return sseResponse([sse({ choices: [{ delta: { content: "ok" } }] }), DONE]);
    }),
  );
  return calls;
}

function pngFile(name = "cat.png", bytes = 8): File {
  return new File([new Uint8Array(bytes)], name, { type: "image/png" });
}

/** Stage a file through the hidden attach input. */
function attach(file: File) {
  const input = screen.getByTestId("attachment-input") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

async function typeMessage(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(screen.getByRole("textbox", { name: /message/i }), text);
}
async function clickSend(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /^send$/i }));
}

function lastUser(body: ChatBody): WireMessage {
  const users = body.messages.filter((m) => m.role === "user");
  return users[users.length - 1];
}

describe("chat-attachments", () => {
  it("test_attach_image_sends_content_parts", async () => {
    const calls = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await typeMessage(user, "what is in this image?");
    attach(pngFile());
    await waitFor(() => expect(screen.getByAltText("cat.png")).toBeInTheDocument());
    await clickSend(user);

    await waitFor(() => expect(calls.length).toBe(1));
    const content = lastUser(calls[0]).content;
    expect(Array.isArray(content)).toBe(true);
    const parts = content as ContentPart[];
    expect(parts[0]).toEqual({ type: "text", text: "what is in this image?" });
    const img = parts.find((p) => p.type === "image_url") as { type: "image_url"; image_url: { url: string } };
    expect(img.image_url.url).toMatch(/^data:image\/png;base64,/);
    // the off-path body keys are unchanged
    expect(calls[0].stream).toBe(true);
    expect(calls[0].stream_options?.include_usage).toBe(true);
  });

  it("test_text_only_stays_string", async () => {
    const calls = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await typeMessage(user, "hello");
    await clickSend(user);

    await waitFor(() => expect(calls.length).toBe(1));
    expect(lastUser(calls[0]).content).toBe("hello");
    expect(JSON.stringify(calls[0])).not.toContain("image_url");
  });

  it("test_remove_staged_preview", async () => {
    const calls = captureChat();
    render(<ChatWorkspace />);

    attach(pngFile("one.png"));
    attach(pngFile("two.png"));
    await waitFor(() => expect(screen.getByAltText("one.png")).toBeInTheDocument());
    expect(screen.getByAltText("two.png")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /remove .*one\.png|remove attachment 1/i }));

    await waitFor(() => expect(screen.queryByAltText("one.png")).not.toBeInTheDocument());
    expect(screen.getByAltText("two.png")).toBeInTheDocument();
    expect(calls.length).toBe(0);
  });

  it("test_committed_turn_renders_text_and_thumb", async () => {
    captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await typeMessage(user, "describe this");
    attach(pngFile("scene.png"));
    await waitFor(() => expect(screen.getByAltText("scene.png")).toBeInTheDocument());
    await clickSend(user);

    // the committed user turn shows the text and an alt-texted thumbnail; the reply commits
    await waitFor(() => expect(screen.getByText("describe this")).toBeInTheDocument());
    expect(screen.getByAltText("scene.png")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("ok")).toBeInTheDocument());
  });

  it("test_staged_clears_after_send", async () => {
    captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await typeMessage(user, "hi");
    attach(pngFile("pic.png"));
    await waitFor(() => expect(screen.getAllByAltText("pic.png").length).toBeGreaterThan(0));
    await clickSend(user);

    // after send: no STAGED preview remains (a thumbnail inside the committed turn is fine),
    // and the composer text is empty
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /remove .*pic\.png|remove attachment/i })).not.toBeInTheDocument(),
    );
    expect((screen.getByRole("textbox", { name: /message/i }) as HTMLTextAreaElement).value).toBe("");
  });

  it("test_oversized_rejected", async () => {
    const calls = captureChat();
    render(<ChatWorkspace />);

    const big = new File([new Uint8Array(MAX_IMAGE_BYTES + 1)], "huge.png", { type: "image/png" });
    attach(big);

    await waitFor(() => expect(screen.getByText(/too large/i)).toBeInTheDocument());
    expect(screen.queryByAltText("huge.png")).not.toBeInTheDocument();
    expect(calls.length).toBe(0);
  });

  it("test_unsupported_type_rejected", async () => {
    const calls = captureChat();
    render(<ChatWorkspace />);

    const pdf = new File([new Uint8Array(4)], "doc.pdf", { type: "application/pdf" });
    attach(pdf);

    await waitFor(() => expect(screen.getByText(/unsupported/i)).toBeInTheDocument());
    expect(screen.queryByAltText("doc.pdf")).not.toBeInTheDocument();
    expect(calls.length).toBe(0);
  });

  it("test_count_cap_rejected", async () => {
    const calls = captureChat();
    render(<ChatWorkspace />);

    for (const n of ["a.png", "b.png", "c.png", "d.png"]) attach(pngFile(n));
    await waitFor(() => expect(screen.getByAltText("d.png")).toBeInTheDocument());
    attach(pngFile("e.png"));

    await waitFor(() => expect(screen.getByText(/limit|maximum|up to/i)).toBeInTheDocument());
    expect(screen.queryByAltText("e.png")).not.toBeInTheDocument();
    // still exactly four staged
    expect(["a.png", "b.png", "c.png", "d.png"].every((n) => screen.getByAltText(n))).toBe(true);
    expect(calls.length).toBe(0);
  });

  it("test_count_cap_holds_under_concurrent_picks", async () => {
    // five picks fired in one synchronous burst (no interposed settle) — the cap
    // must STILL hold at four (the async-pick race is closed by a live-count re-check).
    const calls = captureChat();
    render(<ChatWorkspace />);

    for (const n of ["a.png", "b.png", "c.png", "d.png", "e.png"]) attach(pngFile(n));

    await waitFor(() => expect(screen.getByAltText("d.png")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/limit|maximum|up to/i)).toBeInTheDocument());
    expect(screen.queryByAltText("e.png")).not.toBeInTheDocument();
    expect(["a.png", "b.png", "c.png", "d.png"].every((n) => screen.getByAltText(n))).toBe(true);
    expect(calls.length).toBe(0);
  });
});
