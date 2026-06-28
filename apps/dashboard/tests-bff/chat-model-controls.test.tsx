/**
 * tests-bff/chat-model-controls.test.tsx — RED suite for v40 chat-model-controls.
 *
 * Drives the two SLOTS chat-workspace-page rendered: a catalog-model picker
 * (GET /admin/catalog/models, fail-open) and a collapsed system-prompt + temperature panel
 * that feed the chat-workspace-page send(). Asserts the chosen model / system /
 * temperature reach the chat POST body. RED before Build: ChatWorkspace renders a
 * static model label + no settings panel today, so the combobox / settings toggle
 * are absent → these fail until Build wires ModelPicker + ModelControls.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { ChatWorkspace } from "@/components/chat/ChatWorkspace";

const enc = new TextEncoder();
const URL_CHAT = "http://localhost:3000/api/gw/v1/chat/completions";
const URL_MODELS = "http://localhost:3000/api/gw/admin/catalog/models";
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
  temperature?: number;
  messages: { role: string; content: string }[];
}

/** Register a chat handler that captures the POST body; returns a getter. */
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

async function sendHi(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByRole("textbox", { name: /message/i }), "hi");
  await user.click(screen.getByRole("button", { name: /send/i }));
}

describe("chat-model-controls", () => {
  it("test_picker_lists_and_drives_model", async () => {
    server.use(
      http.get(URL_MODELS, () =>
        HttpResponse.json({ object: "list", data: [{ id: "openai/gpt-4o" }, { id: "anthropic/claude-3" }] }),
      ),
    );
    const box = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    const select = await screen.findByRole("combobox", { name: /model/i });
    await waitFor(() =>
      expect(within(select).getByRole("option", { name: "anthropic/claude-3" })).toBeInTheDocument(),
    );
    await user.selectOptions(select, "anthropic/claude-3");
    await sendHi(user);

    await waitFor(() => expect(box.body?.model).toBe("anthropic/claude-3"));
  });

  it("test_picker_failopen_on_error", async () => {
    server.use(http.get(URL_MODELS, () => HttpResponse.json({ title: "boom", status: 500 }, { status: 500 })));
    const box = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    // fail-open: composer usable, no hard error surfaced
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await sendHi(user);

    await waitFor(() => expect(box.body?.model).toBe("openai/gpt-4o"));
  });

  it("test_system_prompt_feeds_send", async () => {
    const box = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    // The inspector's Parameters tab (default) shows the System prompt control directly.
    await user.type(screen.getByRole("textbox", { name: /system prompt/i }), "Be terse");
    await sendHi(user);

    await waitFor(() => {
      expect(box.body?.messages[0]).toEqual({ role: "system", content: "Be terse" });
      expect(box.body?.messages[1].role).toBe("user");
    });
  });

  it("test_blank_system_no_system_turn", async () => {
    const box = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await sendHi(user);

    await waitFor(() => expect(box.body?.model).toBe("openai/gpt-4o"));
    expect(box.body?.messages.some((m) => m.role === "system")).toBe(false);
  });

  it("test_temperature_feeds_send", async () => {
    const box = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    // The inspector's Parameters tab (default) shows the Temperature control directly.
    fireEvent.change(screen.getByRole("slider", { name: /temperature/i }), { target: { value: "0.2" } });
    await sendHi(user);

    await waitFor(() => expect(box.body?.temperature).toBe(0.2));
  });

  it("test_default_model_used", async () => {
    const box = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await sendHi(user);

    await waitFor(() => expect(box.body?.model).toBe("openai/gpt-4o"));
  });
});
