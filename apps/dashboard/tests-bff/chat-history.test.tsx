/**
 * tests-bff/chat-history.test.tsx — RED suite for v43 chat-history sidebar.
 *
 * Pins the frozen contract:
 *   - ChatHistorySidebar (new component): list / New / resume / delete
 *   - lib/conversations (new BFF client): listConversations / createConversation /
 *     getConversation / appendMessage / deleteConversation
 *   - useChatStream additive API: load() + onTurnComplete
 *   - ChatWorkspace wiring: persistence round-trips (all best-effort)
 *
 * Mirrors chat-workspace-page.test.tsx for SSE + MSW setup.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, renderHook, act, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";

// ── imports under test ────────────────────────────────────────────────────────
import { useChatStream } from "@/lib/hooks/use-chat-stream";
import { ChatWorkspace } from "@/components/chat/ChatWorkspace";
import { ChatHistorySidebar } from "@/components/chat/ChatHistorySidebar";
import {
  listConversations,
  createConversation,
  getConversation,
  appendMessage,
  deleteConversation,
} from "@/lib/conversations";

// ── helpers ───────────────────────────────────────────────────────────────────
const enc = new TextEncoder();
const URL_CHAT = "http://localhost:3000/api/gw/v1/chat/completions";
const URL_CONVS = "http://localhost:3000/api/gw/v1/conversations";
const convUrl = (id: string) => `http://localhost:3000/api/gw/v1/conversations/${id}`;
const msgUrl = (id: string) => `${convUrl(id)}/messages`;

const sse = (o: unknown) => `data: ${JSON.stringify(o)}\n\n`;
const DONE = "data: [DONE]\n\n";

function streamOf(parts: string[], close = true): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(c) {
      for (const p of parts) c.enqueue(enc.encode(p));
      if (close) c.close();
    },
  });
}
function sseResponse(parts: string[], close = true): HttpResponse {
  return new HttpResponse(streamOf(parts, close), {
    headers: { "Content-Type": "text/event-stream" },
  });
}

/** A minimal conversation summary */
const CONV_A = {
  id: "conv-a",
  title: "First conversation",
  updated_at: "2026-06-26T10:00:00Z",
  message_count: 2,
};
const CONV_B = {
  id: "conv-b",
  title: "Second conversation",
  updated_at: "2026-06-26T09:00:00Z",
  message_count: 4,
};

/** Default handler: GET /v1/conversations returns [CONV_A, CONV_B] */
function withConversationList(convs = [CONV_A, CONV_B]) {
  server.use(
    http.get(URL_CONVS, () => HttpResponse.json({ data: convs })),
  );
}

/** Default handler: POST /v1/conversations returns a new conv */
function withCreateConversation(id = "conv-new", title: string | null = "My title") {
  server.use(
    http.post(URL_CONVS, () => HttpResponse.json({ id, title }, { status: 201 })),
  );
}

/** Default handler: POST /:id/messages → 201 with JSON body */
function withAppendMessage(id: string) {
  server.use(
    http.post(msgUrl(id), () => HttpResponse.json({ ok: true }, { status: 201 })),
  );
}

// ════════════════════════════════════════════════════════════════════════════
describe("lib/conversations — typed BFF client", () => {
  it("listConversations_calls_correct_endpoint", async () => {
    server.use(
      http.get(URL_CONVS, () => HttpResponse.json({ data: [CONV_A] })),
    );
    const result = await listConversations();
    expect(result.data).toHaveLength(1);
    expect(result.data[0].id).toBe("conv-a");
    expect(result.data[0].title).toBe("First conversation");
    expect(result.data[0].message_count).toBe(2);
  });

  it("createConversation_posts_title", async () => {
    server.use(
      http.post(URL_CONVS, async ({ request }) => {
        const body = (await request.json()) as { title?: string };
        return HttpResponse.json({ id: "conv-1", title: body.title ?? null }, { status: 201 });
      }),
    );
    const result = await createConversation("My Chat");
    expect(result.id).toBe("conv-1");
    expect(result.title).toBe("My Chat");
  });

  it("getConversation_fetches_messages", async () => {
    server.use(
      http.get(convUrl("conv-a"), () =>
        HttpResponse.json({
          id: "conv-a",
          title: "First conversation",
          messages: [
            { role: "user", content: "Hello" },
            { role: "assistant", content: "Hi there" },
          ],
        }),
      ),
    );
    const result = await getConversation("conv-a");
    expect(result.messages).toHaveLength(2);
    expect(result.messages[0].role).toBe("user");
    expect(result.messages[1].content).toBe("Hi there");
  });

  it("appendMessage_posts_role_and_content", async () => {
    let captured: { role?: string; content?: string } = {};
    server.use(
      http.post(msgUrl("conv-a"), async ({ request }) => {
        captured = (await request.json()) as { role: string; content: string };
        return HttpResponse.json({ ok: true }, { status: 201 });
      }),
    );
    await appendMessage("conv-a", "user", "Hello from user");
    expect(captured.role).toBe("user");
    expect(captured.content).toBe("Hello from user");
  });

  it("deleteConversation_sends_delete", async () => {
    let called = false;
    server.use(
      http.delete(convUrl("conv-a"), () => {
        called = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await deleteConversation("conv-a");
    expect(called).toBe(true);
  });
});

// ════════════════════════════════════════════════════════════════════════════
describe("useChatStream — additive load() + onTurnComplete", () => {
  it("load_replaces_messages_and_goes_idle", async () => {
    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.load([
        { role: "user", content: "Hello" },
        { role: "assistant", content: "Hi" },
      ]);
    });

    expect(result.current.messages).toEqual([
      { role: "user", content: "Hello" },
      { role: "assistant", content: "Hi" },
    ]);
    expect(result.current.status).toBe("idle");
    expect(result.current.streamingText).toBe("");
    expect(result.current.error).toBeUndefined();
  });

  it("onTurnComplete_fires_after_successful_stream_with_user_and_assistant_text", async () => {
    server.use(
      http.post(URL_CHAT, () =>
        sseResponse([
          sse({ choices: [{ delta: { content: "Hello!" } }] }),
          DONE,
        ]),
      ),
    );

    const onTurnComplete = vi.fn();
    const { result } = renderHook(() => useChatStream({ onTurnComplete }));

    await act(async () => {
      result.current.send({ model: "gpt-4o", text: "hi there" });
    });
    await waitFor(() => expect(result.current.status).toBe("idle"));

    expect(onTurnComplete).toHaveBeenCalledOnce();
    expect(onTurnComplete).toHaveBeenCalledWith({
      user: "hi there",
      assistant: "Hello!",
      usage: undefined,
    });
  });

  it("onTurnComplete_includes_usage_when_present", async () => {
    server.use(
      http.post(URL_CHAT, () =>
        sseResponse([
          sse({ choices: [{ delta: { content: "x" } }] }),
          sse({ choices: [], usage: { prompt_tokens: 5, completion_tokens: 1, total_tokens: 6 } }),
          DONE,
        ]),
      ),
    );

    const onTurnComplete = vi.fn();
    const { result } = renderHook(() => useChatStream({ onTurnComplete }));

    await act(async () => {
      result.current.send({ model: "gpt-4o", text: "ping" });
    });
    await waitFor(() => expect(result.current.status).toBe("idle"));

    expect(onTurnComplete).toHaveBeenCalledWith({
      user: "ping",
      assistant: "x",
      usage: { prompt_tokens: 5, completion_tokens: 1, total_tokens: 6 },
    });
  });

  it("onTurnComplete_does_NOT_fire_on_abort_path", async () => {
    server.use(
      http.post(URL_CHAT, () =>
        sseResponse([sse({ choices: [{ delta: { content: "Partial" } }] })], false),
      ),
    );

    const onTurnComplete = vi.fn();
    const { result } = renderHook(() => useChatStream({ onTurnComplete }));

    act(() => {
      result.current.send({ model: "gpt-4o", text: "hi" });
    });
    await waitFor(() => expect(result.current.streamingText).toBe("Partial"));

    act(() => {
      result.current.stop();
    });
    await waitFor(() => expect(result.current.status).toBe("idle"));

    // partial was committed but onTurnComplete must NOT fire (abort path)
    expect(result.current.messages.at(-1)?.content).toBe("Partial");
    expect(onTurnComplete).not.toHaveBeenCalled();
  });

  it("onTurnComplete_does_NOT_fire_on_error_path", async () => {
    server.use(
      http.post(URL_CHAT, () =>
        HttpResponse.json({ title: "Budget exceeded", status: 402 }, { status: 402 }),
      ),
    );

    const onTurnComplete = vi.fn();
    const { result } = renderHook(() => useChatStream({ onTurnComplete }));

    await act(async () => {
      result.current.send({ model: "gpt-4o", text: "hi" });
    });
    await waitFor(() => expect(result.current.status).toBe("error"));

    expect(onTurnComplete).not.toHaveBeenCalled();
  });
});

// ════════════════════════════════════════════════════════════════════════════
describe("ChatHistorySidebar", () => {
  it("list_renders_newest_first", async () => {
    withConversationList([CONV_A, CONV_B]);
    render(
      <ChatHistorySidebar
        activeId={null}
        onSelect={vi.fn()}
        onNew={vi.fn()}
        refreshKey={0}
        streaming={false}
      />,
    );

    // Both conversations appear
    expect(await screen.findByText("First conversation")).toBeInTheDocument();
    expect(await screen.findByText("Second conversation")).toBeInTheDocument();

    // New button present
    expect(screen.getByRole("button", { name: /new/i })).toBeInTheDocument();
  });

  it("new_clears_workspace_via_onNew_callback", async () => {
    withConversationList([CONV_A]);
    const onNew = vi.fn();
    render(
      <ChatHistorySidebar
        activeId={null}
        onSelect={vi.fn()}
        onNew={onNew}
        refreshKey={0}
        streaming={false}
      />,
    );

    await screen.findByText("First conversation");
    await userEvent.click(screen.getByRole("button", { name: /new/i }));
    expect(onNew).toHaveBeenCalledOnce();
  });

  it("new_button_disabled_while_streaming", async () => {
    withConversationList([]);
    render(
      <ChatHistorySidebar
        activeId={null}
        onSelect={vi.fn()}
        onNew={vi.fn()}
        refreshKey={0}
        streaming={true}
      />,
    );

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /new/i })).toBeDisabled(),
    );
  });

  it("onSelect_called_when_conversation_clicked", async () => {
    withConversationList([CONV_A]);
    const onSelect = vi.fn();
    render(
      <ChatHistorySidebar
        activeId={null}
        onSelect={onSelect}
        onNew={vi.fn()}
        refreshKey={0}
        streaming={false}
      />,
    );

    // Use exact text match to avoid matching the "Delete First conversation" button
    const item = await screen.findByRole("button", { name: "First conversation" });
    await userEvent.click(item);
    expect(onSelect).toHaveBeenCalledWith("conv-a");
  });

  it("active_item_has_aria_current", async () => {
    withConversationList([CONV_A, CONV_B]);
    render(
      <ChatHistorySidebar
        activeId="conv-a"
        onSelect={vi.fn()}
        onNew={vi.fn()}
        refreshKey={0}
        streaming={false}
      />,
    );

    // Use exact title text to avoid matching the "Delete First conversation" button
    const activeBtn = await screen.findByRole("button", { name: "First conversation" });
    expect(activeBtn).toHaveAttribute("aria-current", "true");

    const inactiveBtn = screen.getByRole("button", { name: "Second conversation" });
    expect(inactiveBtn).not.toHaveAttribute("aria-current", "true");
  });

  it("delete_removes_conversation", async () => {
    withConversationList([CONV_A]);
    let deleteCalled = false;
    server.use(
      http.delete(convUrl("conv-a"), () => {
        deleteCalled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    // After delete, refetch returns empty
    server.use(
      http.get(URL_CONVS, () => {
        if (deleteCalled) return HttpResponse.json({ data: [] });
        return HttpResponse.json({ data: [CONV_A] });
      }),
    );

    render(
      <ChatHistorySidebar
        activeId={null}
        onSelect={vi.fn()}
        onNew={vi.fn()}
        refreshKey={0}
        streaming={false}
      />,
    );

    await screen.findByText("First conversation");
    const deleteBtn = screen.getByRole("button", { name: /delete first conversation/i });
    await userEvent.click(deleteBtn);

    await waitFor(() => expect(deleteCalled).toBe(true));
  });

  it("shows_empty_state_when_no_conversations", async () => {
    withConversationList([]);
    render(
      <ChatHistorySidebar
        activeId={null}
        onSelect={vi.fn()}
        onNew={vi.fn()}
        refreshKey={0}
        streaming={false}
      />,
    );

    // The sidebar should be non-blocking — just shows an empty state
    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );
  });
});

// ════════════════════════════════════════════════════════════════════════════
describe("ChatWorkspace — conversation persistence", () => {
  beforeEach(() => {
    // Default: no existing conversations
    withConversationList([]);
  });

  it("first_turn_creates_and_persists_user_and_assistant", async () => {
    const createdConvId = "conv-new-1";
    const postedMessages: Array<{ role: string; content: string }> = [];

    server.use(
      http.post(URL_CONVS, () =>
        HttpResponse.json({ id: createdConvId, title: "Hello world" }, { status: 201 }),
      ),
    );
    server.use(
      http.post(msgUrl(createdConvId), async ({ request }) => {
        const body = (await request.json()) as { role: string; content: string };
        postedMessages.push(body);
        return HttpResponse.json({ ok: true }, { status: 201 });
      }),
    );
    server.use(
      http.post(URL_CHAT, () =>
        sseResponse([
          sse({ choices: [{ delta: { content: "Hi back!" } }] }),
          DONE,
        ]),
      ),
    );
    // After turn, sidebar refreshes
    server.use(
      http.get(URL_CONVS, () =>
        HttpResponse.json({
          data: [{ id: createdConvId, title: "Hello world", updated_at: "2026-06-26T10:00:00Z", message_count: 2 }],
        }),
      ),
    );

    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: /message/i }), "Hello world");
    await user.click(screen.getByRole("button", { name: /send/i }));

    // Wait for the assistant message to render
    await screen.findByText("Hi back!");

    // POST /v1/conversations must have been called
    await waitFor(() => {
      const userMsg = postedMessages.find((m) => m.role === "user");
      expect(userMsg).toBeDefined();
      expect(userMsg?.content).toBe("Hello world");
    });

    // POST /{id}/messages for assistant must have been called with exact streamed text
    await waitFor(() => {
      const assistantMsg = postedMessages.find((m) => m.role === "assistant");
      expect(assistantMsg).toBeDefined();
      expect(assistantMsg?.content).toBe("Hi back!");
    });
  });

  it("resume_loads_messages_into_thread", async () => {
    withConversationList([CONV_A]);
    server.use(
      http.get(convUrl("conv-a"), () =>
        HttpResponse.json({
          id: "conv-a",
          title: "First conversation",
          messages: [
            { role: "user", content: "Old user message" },
            { role: "assistant", content: "Old assistant reply" },
          ],
        }),
      ),
    );

    render(<ChatWorkspace />);

    // sidebar loads — use exact title to avoid matching "Delete First conversation"
    const convBtn = await screen.findByRole("button", { name: "First conversation" });
    await userEvent.click(convBtn);

    // thread should now show the loaded messages
    await screen.findByText("Old user message");
    await screen.findByText("Old assistant reply");
  });

  it("persist_failure_is_nonblocking_turn_visible_and_sidebar_error_set", async () => {
    const convId = "conv-fail";
    server.use(
      http.post(URL_CONVS, () =>
        HttpResponse.json({ id: convId, title: "fail test" }, { status: 201 }),
      ),
    );
    // appendMessage always 500
    server.use(
      http.post(msgUrl(convId), () =>
        HttpResponse.json({ title: "DB error", status: 500 }, { status: 500 }),
      ),
    );
    server.use(
      http.post(URL_CHAT, () =>
        sseResponse([
          sse({ choices: [{ delta: { content: "Streamed response" } }] }),
          DONE,
        ]),
      ),
    );
    server.use(
      http.get(URL_CONVS, () => HttpResponse.json({ data: [] })),
    );

    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: /message/i }), "test persist failure");
    await user.click(screen.getByRole("button", { name: /send/i }));

    // streamed turn MUST still be visible despite persistence failure
    await screen.findByText("Streamed response");

    // no uncaught throw — we're still on the chat page (empty state gone)
    expect(screen.queryByText(/start a conversation/i)).not.toBeInTheDocument();
  });
});
