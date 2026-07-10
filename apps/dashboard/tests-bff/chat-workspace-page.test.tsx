/**
 * tests-bff/chat-workspace-page.test.tsx — RED suite for v40 chat-workspace-page.
 *
 * Pins the frozen §3 contract: the net-new useChatStream SSE hook (the seam the
 * sibling tasks import), the ChatWorkspace four-state UI, and the role-open nav
 * entry. RED before Build: useChatStream + ChatWorkspace don't exist yet, so the
 * imports fail at collect — the whole file is red until Build creates them.
 *
 * Runs in the "bff" vitest project (jsdom origin http://localhost:3000; the hook
 * fetches the same absolute base the BFF client uses, so MSW intercepts it). SSE
 * is mocked with a ReadableStream body per tests-bff/streaming-bff.test.ts.
 */

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, renderHook, act, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { BffError } from "@/lib/bff-client";

// ── RED: these modules do not exist until Build ────────────────────────────────
import { useChatStream } from "@/lib/hooks/use-chat-stream";
import { ChatWorkspace } from "@/components/chat/ChatWorkspace";
import { NAV_ITEMS } from "@/components/ui/app-shell";

// logs-explorer-ui (v58): the sessionStorage-mediated replay handoff this suite's
// "replay handoff" describe block below exercises from the ChatWorkspace (consuming)
// side — LogDetailDrawer's writing side is covered in tests/logs.test.tsx.
import { writeReplayPayload } from "@/lib/logs-replay";

// ── helpers ───────────────────────────────────────────────────────────────────
const enc = new TextEncoder();
const URL_CHAT = "http://localhost:3000/api/gw/v1/chat/completions";
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

// ════════════════════════════════════════════════════════════════════════════
describe("useChatStream — the frozen SSE seam", () => {
  it("test_streams_token_by_token", async () => {
    server.use(
      http.post(URL_CHAT, () =>
        sseResponse([
          sse({ choices: [{ delta: { role: "assistant" } }] }),
          sse({ choices: [{ delta: { content: "Hel" } }] }),
          sse({ choices: [{ delta: { content: "lo" } }] }),
          sse({ choices: [], usage: { prompt_tokens: 5, completion_tokens: 2, total_tokens: 7 } }),
          DONE,
        ]),
      ),
    );

    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      result.current.send({ model: "gpt-4o", text: "hi" });
    });
    await waitFor(() => expect(result.current.status).toBe("idle"));

    expect(result.current.messages).toEqual([
      { role: "user", content: "hi" },
      { role: "assistant", content: "Hello" },
    ]);
    expect(result.current.streamingText).toBe("");
  });

  it("test_usage_captured", async () => {
    server.use(
      http.post(URL_CHAT, () =>
        sseResponse([
          sse({ choices: [{ delta: { content: "x" } }] }),
          sse({ choices: [], usage: { prompt_tokens: 11, completion_tokens: 4, total_tokens: 15 } }),
          DONE,
        ]),
      ),
    );

    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      result.current.send({ model: "gpt-4o", text: "hi" });
    });
    await waitFor(() => expect(result.current.status).toBe("idle"));

    expect(result.current.usage).toEqual({ prompt_tokens: 11, completion_tokens: 4, total_tokens: 15 });
  });

  it("test_absent_usage_stays_undefined", async () => {
    server.use(
      http.post(URL_CHAT, () => sseResponse([sse({ choices: [{ delta: { content: "Hi" } }] }), DONE])),
    );

    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      result.current.send({ model: "gpt-4o", text: "hi" });
    });
    await waitFor(() => expect(result.current.status).toBe("idle"));

    expect(result.current.usage).toBeUndefined();
    expect(result.current.messages.at(-1)).toEqual({ role: "assistant", content: "Hi" });
  });

  it("test_stop_commits_partial", async () => {
    // a stream that emits one delta then stays open until aborted
    server.use(
      http.post(URL_CHAT, () => sseResponse([sse({ choices: [{ delta: { content: "Partial" } }] })], false)),
    );

    const { result } = renderHook(() => useChatStream());
    act(() => {
      result.current.send({ model: "gpt-4o", text: "hi" });
    });
    await waitFor(() => expect(result.current.streamingText).toBe("Partial"));

    act(() => {
      result.current.stop();
    });
    await waitFor(() => expect(result.current.status).toBe("idle"));

    expect(result.current.messages.at(-1)).toEqual({ role: "assistant", content: "Partial" });
    expect(result.current.streamingText).toBe("");
  });

  it("test_sse_fragmentation_byte_by_byte", async () => {
    const frame = sse({ choices: [{ delta: { content: "Fragmented" } }] }) + DONE;
    const bytes = Array.from(enc.encode(frame)).map((b) => new Uint8Array([b]));
    server.use(
      http.post(URL_CHAT, () =>
        new HttpResponse(
          new ReadableStream({
            start(c) {
              for (const b of bytes) c.enqueue(b);
              c.close();
            },
          }),
          { headers: { "Content-Type": "text/event-stream" } },
        ),
      ),
    );

    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      result.current.send({ model: "gpt-4o", text: "hi" });
    });
    await waitFor(() => expect(result.current.status).toBe("idle"));

    expect(result.current.messages.at(-1)).toEqual({ role: "assistant", content: "Fragmented" });
  });

  it("test_unmount_aborts_in_flight_stream", async () => {
    // design-for-failure: navigating away mid-stream MUST abort the fetch so the
    // BFF closes and the gateway's v35 disconnect-billing fires (no cost leak).
    let capturedSignal: AbortSignal | undefined;
    server.use(
      http.post(URL_CHAT, ({ request }) => {
        capturedSignal = request.signal;
        return sseResponse([sse({ choices: [{ delta: { content: "Partial" } }] })], false); // never closes
      }),
    );

    const { result, unmount } = renderHook(() => useChatStream());
    act(() => {
      result.current.send({ model: "gpt-4o", text: "hi" });
    });
    await waitFor(() => expect(result.current.streamingText).toBe("Partial"));

    unmount();
    await waitFor(() => expect(capturedSignal?.aborted).toBe(true));
  });

  it("test_send_after_error_recovers", async () => {
    // a failed turn must not wedge the hook: a subsequent send clears error and streams.
    server.use(
      http.post(URL_CHAT, () =>
        HttpResponse.json({ title: "Budget exceeded", status: 402 }, { status: 402 }),
      ),
    );
    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      result.current.send({ model: "gpt-4o", text: "first" });
    });
    await waitFor(() => expect(result.current.status).toBe("error"));

    server.use(http.post(URL_CHAT, () => sseResponse([sse({ choices: [{ delta: { content: "ok" } }] }), DONE])));
    await act(async () => {
      result.current.send({ model: "gpt-4o", text: "second" });
    });
    await waitFor(() => expect(result.current.status).toBe("idle"));

    expect(result.current.error).toBeUndefined();
    expect(result.current.messages.at(-1)).toEqual({ role: "assistant", content: "ok" });
  });

  it("test_non_2xx_sets_error_and_preserves_user_msg", async () => {
    server.use(
      http.post(URL_CHAT, () =>
        HttpResponse.json({ title: "Budget exceeded", status: 402, code: "ERR_BUDGET_EXCEEDED" }, { status: 402 }),
      ),
    );

    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      result.current.send({ model: "gpt-4o", text: "hi" });
    });
    await waitFor(() => expect(result.current.status).toBe("error"));

    expect(result.current.error).toBeInstanceOf(BffError);
    expect(result.current.error?.status).toBe(402);
    expect(result.current.messages).toEqual([{ role: "user", content: "hi" }]);
  });
});

describe("ChatWorkspace — the four UI states + composer", () => {
  it("test_empty_state_then_streams_success", async () => {
    server.use(
      http.post(URL_CHAT, () => sseResponse([sse({ choices: [{ delta: { content: "Hello there" } }] }), DONE])),
    );
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    // empty state on mount
    expect(screen.getByText(/start a conversation/i)).toBeInTheDocument();

    await user.type(screen.getByRole("textbox", { name: /message/i }), "hi");
    await user.click(screen.getByRole("button", { name: /send/i }));

    expect(await screen.findByText("hi")).toBeInTheDocument();
    expect(await screen.findByText("Hello there")).toBeInTheDocument();
  });

  it("test_error_state_renders_alert", async () => {
    server.use(
      http.post(URL_CHAT, () =>
        HttpResponse.json({ title: "Budget exceeded", status: 402, code: "ERR_BUDGET_EXCEEDED" }, { status: 402 }),
      ),
    );
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: /message/i }), "hi");
    await user.click(screen.getByRole("button", { name: /send/i }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("test_empty_submit_is_noop", async () => {
    let called = false;
    server.use(
      http.post(URL_CHAT, () => {
        called = true;
        return sseResponse([DONE]);
      }),
    );
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await user.click(screen.getByRole("button", { name: /send/i }));

    expect(called).toBe(false);
    expect(screen.queryByText(/^hi$/)).not.toBeInTheDocument();
  });
});

describe("replay handoff from Logs Explorer (logs-explorer-ui, M6/M8)", () => {
  // The default `/api/gw/admin/catalog/models` handler (tests-bff/mocks/handlers.ts)
  // returns exactly [{ id: "openai/gpt-4o" }] unless a test overrides it — used below
  // to exercise both the in-catalog and fallen-back-to-default paths without extra mocks.
  afterEach(() => {
    window.sessionStorage.clear();
  });

  it("test_replay_prefills_composer_and_model_without_sending", async () => {
    let chatCalled = false;
    server.use(
      http.post(URL_CHAT, () => {
        chatCalled = true;
        return sseResponse([sse({ choices: [{ delta: { content: "ok" } }] }), DONE]);
      }),
    );
    writeReplayPayload({ text: "replay me please", modelId: "openai/gpt-4o", degraded: false });

    render(<ChatWorkspace />);

    const textarea = (await screen.findByRole("textbox", {
      name: /message/i,
    })) as HTMLTextAreaElement;
    await waitFor(() => expect(textarea).toHaveValue("replay me please"));

    const select = screen.getByRole("combobox", { name: /model/i }) as HTMLSelectElement;
    await waitFor(() => expect(select).toHaveValue("openai/gpt-4o"));

    // M6: pre-fill only — never auto-sent. The empty state (no messages yet) still
    // shows, and the chat POST never fires just from consuming the payload.
    expect(screen.getByText(/start a conversation/i)).toBeInTheDocument();
    expect(chatCalled).toBe(false);
  });

  it("test_replay_falls_back_to_default_model_when_replayed_model_not_in_live_catalog", async () => {
    // default catalog handler only knows "openai/gpt-4o" — this modelId is absent.
    writeReplayPayload({ text: "hi from a vanished model", modelId: "mystery/vanished-model", degraded: false });

    render(<ChatWorkspace />);

    const select = (await screen.findByRole("combobox", { name: /model/i })) as HTMLSelectElement;
    await waitFor(() => expect(select).toHaveValue("openai/gpt-4o"));

    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent(/model no longer available/i);
    expect(notice).toHaveTextContent(/openai\/gpt-4o/);
  });

  it("test_replay_degraded_content_shows_a_notice", async () => {
    writeReplayPayload({ text: "only the text part survived", modelId: "openai/gpt-4o", degraded: true });

    render(<ChatWorkspace />);

    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: /message/i })).toHaveValue("only the text part survived"),
    );
    expect(await screen.findByText(/couldn.t be replayed/i)).toBeInTheDocument();
  });

  it("test_replay_payload_is_consumed_exactly_once", async () => {
    writeReplayPayload({ text: "one shot only", modelId: "openai/gpt-4o", degraded: false });

    const first = render(<ChatWorkspace />);
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: /message/i })).toHaveValue("one shot only"),
    );
    first.unmount();

    // A second mount (e.g. back-navigation) must NOT re-apply the same payload —
    // consumeReplayPayload() already cleared it on the first read.
    render(<ChatWorkspace />);
    await waitFor(() => expect(screen.getByRole("textbox", { name: /message/i })).toHaveValue(""));
  });

  it("test_replay_does_not_clobber_in_progress_typing", async () => {
    // The catalog resolves async and gates the replay-apply block. A user who starts
    // typing before it settles must keep their draft — the pre-fill is a convenience,
    // never an override. Gate the catalog so the composer is interactive first.
    let releaseCatalog: () => void = () => {};
    const catalogGate = new Promise<void>((resolve) => {
      releaseCatalog = resolve;
    });
    server.use(
      http.get("http://localhost:3000/api/gw/admin/catalog/models", async () => {
        await catalogGate;
        return HttpResponse.json({
          object: "list",
          data: [{ id: "openai/gpt-4o", name: "GPT-4o", context_length: 128000 }],
        });
      }),
    );
    writeReplayPayload({ text: "REPLAY MUST NOT WIN", modelId: "openai/gpt-4o", degraded: false });

    const user = userEvent.setup();
    render(<ChatWorkspace />);

    const textarea = (await screen.findByRole("textbox", {
      name: /message/i,
    })) as HTMLTextAreaElement;
    await user.type(textarea, "my own draft");
    expect(textarea).toHaveValue("my own draft");

    // Let the catalog resolve — the replay-apply block now runs in the same commit that
    // renders the catalog options; the guarded block must leave the draft intact.
    releaseCatalog();
    const select = screen.getByRole("combobox", { name: /model/i });
    await waitFor(() =>
      expect(within(select).getByRole("option", { name: /gpt-4o/i })).toBeInTheDocument(),
    );
    expect(textarea).toHaveValue("my own draft");
  });

  it("test_no_replay_payload_leaves_composer_untouched", async () => {
    render(<ChatWorkspace />);
    await waitFor(() => expect(screen.getByRole("combobox", { name: /model/i })).toBeInTheDocument());
    expect(screen.getByRole("textbox", { name: /message/i })).toHaveValue("");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});

describe("nav entry", () => {
  it("test_nav_entry_visible_to_all_roles", () => {
    const chat = NAV_ITEMS.find((i) => i.href === "/app/chat");
    expect(chat).toBeDefined();
    expect(chat?.label).toBe("Chat");
    expect(chat?.minRole).toBeUndefined();
  });
});
