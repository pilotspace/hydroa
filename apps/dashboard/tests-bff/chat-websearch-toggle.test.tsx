/**
 * tests-bff/chat-websearch-toggle.test.tsx — RED suite for v41 chat-websearch-toggle.
 *
 * A "Web search" toggle in the chat controls sets web_search:true on the chat POST
 * body (which the v41 gateway translates to provider-native grounding). Off by
 * default ⇒ the body is byte-identical to v40 (no web_search key). RED before Build:
 * ModelControls has no web-search toggle and SendInput/runStream don't emit the flag.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { server } from "./mocks/server";
import { HttpResponse } from "msw";
import { ChatWorkspace } from "@/components/chat/ChatWorkspace";

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

interface ChatBody {
  model: string;
  stream?: boolean;
  stream_options?: { include_usage?: boolean };
  web_search?: boolean;
  messages: { role: string; content: string }[];
}

function captureChat() {
  const box: { body?: ChatBody } = {};
  server.use(
    http.post(URL_CHAT, async ({ request }) => {
      box.body = (await request.json()) as ChatBody;
      return sseResponse([sse({ choices: [{ delta: { content: "ok" } }] }), DONE]);
    }),
  );
  return box;
}

async function openControls(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /model settings/i }));
}

async function sendHi(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByRole("textbox", { name: /message/i }), "hi");
  await user.click(screen.getByRole("button", { name: /send/i }));
}

describe("chat-websearch-toggle", () => {
  it("test_toggle_present", async () => {
    const user = userEvent.setup();
    render(<ChatWorkspace />);
    await openControls(user);
    const toggle = screen.getByRole("checkbox", { name: /web search/i });
    expect(toggle).toBeInTheDocument();
    expect(toggle).not.toBeDisabled(); // keyboard-operable native control
  });

  it("test_toggle_on_sends_flag", async () => {
    const box = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);
    await openControls(user);
    await user.click(screen.getByRole("checkbox", { name: /web search/i }));
    await sendHi(user);
    await waitFor(() => expect(box.body?.web_search).toBe(true));
  });

  it("test_toggle_off_no_flag", async () => {
    const box = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);
    await sendHi(user);
    await waitFor(() => expect(box.body?.model).toBe("openai/gpt-4o"));
    // off by default ⇒ byte-identical: no web_search key, v40 fields intact
    expect("web_search" in (box.body ?? {})).toBe(false);
    expect(box.body?.stream).toBe(true);
    expect(box.body?.messages.at(-1)?.role).toBe("user");
  });
});
