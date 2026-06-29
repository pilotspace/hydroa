/**
 * tests-bff/use-chat-stream-tools.test.ts — hook-level invariants for chat-tools-functions.
 *
 * Complements the UI suite (chat-tools.test.tsx) by pinning the streaming-consumer
 * contract directly at the useChatStream seam — the invariants the UI can only observe
 * indirectly:
 *   - a tool-call turn enters "awaiting_tool" exposing pendingToolCalls and commits the
 *     assistant tool_calls message — WITHOUT firing onTurnComplete or committing an empty
 *     content turn (the dropped-empty-turn bug the contract forbids);
 *   - continuing with results re-streams; when the continuation returns CONTENT,
 *     onTurnComplete fires exactly once for the completed turn.
 */

import { describe, it, expect, vi } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { useChatStream } from "@/lib/hooks/use-chat-stream";

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

const TOOL_CALL_STREAM = [
  sse({ choices: [{ delta: { tool_calls: [{ index: 0, id: "call_1", type: "function", function: { name: "get_weather", arguments: "" } }] } }] }),
  sse({ choices: [{ delta: { tool_calls: [{ index: 0, function: { arguments: '{"city":"NYC"}' } }] } }] }),
  sse({ choices: [{ delta: {}, finish_reason: "tool_calls" }] }),
  DONE,
];

interface Body {
  messages: Array<{ role: string; content: string; tool_call_id?: string }>;
}

describe("useChatStream · tool calls", () => {
  it("tool turn enters awaiting_tool WITHOUT firing onTurnComplete or an empty turn", async () => {
    server.use(http.post(URL_CHAT, () => sseResponse(TOOL_CALL_STREAM)));
    const onTurnComplete = vi.fn();
    const { result } = renderHook(() => useChatStream({ onTurnComplete }));

    act(() => result.current.send({ model: "openai/gpt-4o", text: "weather in NYC?" }));

    await waitFor(() => expect(result.current.status).toBe("awaiting_tool"));

    // the pending call is exposed for the operator
    expect(result.current.pendingToolCalls).toEqual([
      { id: "call_1", name: "get_weather", arguments: '{"city":"NYC"}' },
    ]);
    // the assistant tool_calls message is committed — NOT an empty content bubble
    const last = result.current.messages[result.current.messages.length - 1];
    expect(last.role).toBe("assistant");
    expect(last.tool_calls?.[0]).toMatchObject({ id: "call_1", function: { name: "get_weather" } });
    // the dropped-empty-turn bug: onTurnComplete MUST NOT fire on a tool-call turn
    expect(onTurnComplete).not.toHaveBeenCalled();
  });

  it("continuing with a result re-streams and fires onTurnComplete once on the content turn", async () => {
    let n = 0;
    const bodies: Body[] = [];
    server.use(
      http.post(URL_CHAT, async ({ request }) => {
        bodies.push((await request.json()) as Body);
        n += 1;
        if (n === 1) return sseResponse(TOOL_CALL_STREAM);
        return sseResponse([sse({ choices: [{ delta: { content: "It is 72F." } }] }), DONE]);
      }),
    );
    const onTurnComplete = vi.fn();
    const { result } = renderHook(() => useChatStream({ onTurnComplete }));

    act(() => result.current.send({ model: "openai/gpt-4o", text: "weather in NYC?" }));
    await waitFor(() => expect(result.current.status).toBe("awaiting_tool"));
    expect(onTurnComplete).not.toHaveBeenCalled();

    act(() => result.current.submitToolResults([{ tool_call_id: "call_1", content: '{"tempF":72}' }]));

    await waitFor(() => expect(result.current.status).toBe("idle"));
    // the continuation carried the role:"tool" answer
    const toolMsg = bodies[1].messages.find((m) => m.role === "tool" && m.tool_call_id === "call_1");
    expect(toolMsg?.content).toBe('{"tempF":72}');
    // the completed CONTENT turn fires onTurnComplete exactly once
    expect(onTurnComplete).toHaveBeenCalledTimes(1);
    expect(onTurnComplete.mock.calls[0][0]).toMatchObject({ assistant: "It is 72F." });
  });
});
