/**
 * tests-bff/chat-tools.test.tsx — RED suite for chat-tools-functions.
 *
 * The inspector Tools tab gains a JSON-editor tool list + an Auto/Required/None
 * tool_choice selector, wired pass-through to POST /api/gw/v1/chat/completions.
 * The streaming consumer assembles delta.tool_calls (by index) + finish_reason
 * "tool_calls", renders each call as a ToolCallCard, and a partial-friendly
 * Continue appends one role:"tool" message PER pending call (unanswered ⇒ "").
 *
 * RED before Build: today SendInput has no tools, the body carries none, and the
 * SSE consumer reads ONLY delta.content (tool_calls frames are silently dropped).
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
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

interface WireMessage {
  role: string;
  content: string;
  tool_call_id?: string;
  tool_calls?: Array<{ id: string; type: string; function: { name: string; arguments: string } }>;
}
interface ChatBody {
  model: string;
  messages: WireMessage[];
  tools?: Array<{ type: string; function: { name: string; description?: string; parameters: unknown } }>;
  tool_choice?: string;
  stream?: boolean;
  stream_options?: { include_usage?: boolean };
}

const TOOL = {
  name: "get_weather",
  description: "Get the weather for a city",
  parameters: { type: "object", properties: { city: { type: "string" } }, required: ["city"] },
};

/** Capture every POST body; reply with `content` SSE by default. */
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

/** First POST returns a tool-call stream; later POSTs return content. */
function captureToolThenContent(toolFrames: string[]) {
  const calls: ChatBody[] = [];
  server.use(
    http.post(URL_CHAT, async ({ request }) => {
      calls.push((await request.json()) as ChatBody);
      if (calls.length === 1) return sseResponse(toolFrames);
      return sseResponse([sse({ choices: [{ delta: { content: "It is 72F." } }] }), DONE]);
    }),
  );
  return calls;
}

const ONE_TOOL_CALL = [
  sse({ choices: [{ delta: { tool_calls: [{ index: 0, id: "call_1", type: "function", function: { name: "get_weather", arguments: "" } }] } }] }),
  sse({ choices: [{ delta: { tool_calls: [{ index: 0, function: { arguments: '{"city":' } }] } }] }),
  sse({ choices: [{ delta: { tool_calls: [{ index: 0, function: { arguments: '"NYC"}' } }] } }] }),
  sse({ choices: [{ delta: {}, finish_reason: "tool_calls" }] }),
  DONE,
];

const TWO_TOOL_CALLS = [
  sse({ choices: [{ delta: { tool_calls: [{ index: 0, id: "call_1", type: "function", function: { name: "get_weather", arguments: "{}" } }] } }] }),
  sse({ choices: [{ delta: { tool_calls: [{ index: 1, id: "call_2", type: "function", function: { name: "get_time", arguments: "{}" } }] } }] }),
  sse({ choices: [{ delta: {}, finish_reason: "tool_calls" }] }),
  DONE,
];

async function defineTool(user: ReturnType<typeof userEvent.setup>, json: string) {
  await user.click(screen.getByRole("tab", { name: /tools/i }));
  await user.click(screen.getByRole("button", { name: /add tool/i }));
  const editors = screen.getAllByRole("textbox", { name: /tool definition/i });
  fireEvent.change(editors[editors.length - 1], { target: { value: json } });
}

async function sendHi(user: ReturnType<typeof userEvent.setup>, text = "hi") {
  await user.type(screen.getByRole("textbox", { name: /message/i }), text);
  await user.click(screen.getByRole("button", { name: /^send$/i }));
}

describe("chat-tools-functions", () => {
  it("test_tools_and_choice_reach_body", async () => {
    const calls = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await defineTool(user, JSON.stringify(TOOL));
    await user.click(screen.getByRole("button", { name: /^required$/i }));
    await sendHi(user);

    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].tools).toEqual([{ type: "function", function: TOOL }]);
    expect(calls[0].tool_choice).toBe("required");
    expect(calls[0].stream).toBe(true);
    expect(calls[0].stream_options?.include_usage).toBe(true);
  });

  it("test_no_tools_omitted_byte_identical", async () => {
    const calls = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await sendHi(user);

    await waitFor(() => expect(calls.length).toBe(1));
    expect("tools" in calls[0]).toBe(false);
    expect("tool_choice" in calls[0]).toBe(false);
  });

  it("test_invalid_tool_excluded", async () => {
    const calls = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await defineTool(user, "{ this is not json");
    // the editor flags it invalid
    expect(screen.getByText(/invalid/i)).toBeInTheDocument();
    await sendHi(user);

    await waitFor(() => expect(calls.length).toBe(1));
    // the only entry is invalid → no tools key ships
    expect("tools" in calls[0]).toBe(false);
  });

  it("test_mixed_valid_invalid_only_valid_ships", async () => {
    const calls = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    // one valid tool + one malformed draft → only the valid one reaches the wire
    await defineTool(user, JSON.stringify(TOOL));
    await defineTool(user, "{ broken");
    expect(screen.getByText(/invalid/i)).toBeInTheDocument();
    await sendHi(user);

    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].tools).toEqual([{ type: "function", function: TOOL }]);
  });

  it("test_tool_calls_render_not_dropped", async () => {
    captureToolThenContent(ONE_TOOL_CALL);
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await defineTool(user, JSON.stringify(TOOL));
    await sendHi(user);

    // the assembled tool_call renders as a card (name + args + id), with a Continue affordance —
    // NOT a dropped/empty assistant turn
    await waitFor(() => expect(screen.getByText("get_weather")).toBeInTheDocument());
    expect(screen.getByText(/call_1/)).toBeInTheDocument();
    expect(screen.getByText(/NYC/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /continue/i })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /tool result/i })).toBeInTheDocument();
  });

  it("test_supply_result_continues", async () => {
    const calls = captureToolThenContent(ONE_TOOL_CALL);
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await defineTool(user, JSON.stringify(TOOL));
    await sendHi(user);
    await waitFor(() => expect(screen.getByText("get_weather")).toBeInTheDocument());

    fireEvent.change(screen.getByRole("textbox", { name: /tool result/i }), { target: { value: '{"tempF":72}' } });
    await user.click(screen.getByRole("button", { name: /continue/i }));

    await waitFor(() => expect(calls.length).toBe(2));
    const msgs = calls[1].messages;
    // the assistant tool_calls message is followed by a matching role:"tool" result
    const toolMsg = msgs.find((m) => m.role === "tool" && m.tool_call_id === "call_1");
    expect(toolMsg?.content).toBe('{"tempF":72}');
    const asst = msgs.find((m) => m.role === "assistant" && m.tool_calls?.some((t) => t.id === "call_1"));
    expect(asst).toBeTruthy();
    // tools are still sent on the continuation
    expect(calls[1].tools).toEqual([{ type: "function", function: TOOL }]);
  });

  it("test_partial_continue_answers_all", async () => {
    const calls = captureToolThenContent(TWO_TOOL_CALLS);
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await defineTool(user, JSON.stringify(TOOL));
    await sendHi(user);
    await waitFor(() => expect(screen.getByText("get_weather")).toBeInTheDocument());
    expect(screen.getByText("get_time")).toBeInTheDocument();

    // answer ONLY the first call, then continue
    const results = screen.getAllByRole("textbox", { name: /tool result/i });
    fireEvent.change(results[0], { target: { value: "sunny" } });
    await user.click(screen.getByRole("button", { name: /continue/i }));

    await waitFor(() => expect(calls.length).toBe(2));
    const toolMsgs = calls[1].messages.filter((m) => m.role === "tool");
    // EVERY pending call is answered (the unanswered one carries an empty placeholder)
    expect(toolMsgs.find((m) => m.tool_call_id === "call_1")?.content).toBe("sunny");
    expect(toolMsgs.find((m) => m.tool_call_id === "call_2")?.content).toBe("");
  });

  it("test_tool_choice_maps", async () => {
    const calls = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await defineTool(user, JSON.stringify(TOOL));
    await user.click(screen.getByRole("button", { name: /^none$/i }));
    await sendHi(user);
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].tool_choice).toBe("none");
  });
});
