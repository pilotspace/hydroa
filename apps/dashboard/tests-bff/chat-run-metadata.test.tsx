/**
 * tests-bff/chat-run-metadata.test.tsx — RED/GREEN suite for chat-run-metadata-cost task.
 *
 * Contract under test (§3 CONTRACT):
 *   - TurnMeta.finishReason captured from SSE choices[0].finish_reason
 *   - Per-turn meta line shows: model · finish_reason · Xp / Yc Zt · latency · $cost
 *   - CostReadout.sessionCost shows "$X.XXXX session" (sum of per-turn costs)
 *   - Honest-absent: no usage → no cost/tokens; no priceMap entry → tokens OK, no cost
 *
 * Test pattern mirrors chat-cost-readout.test.tsx: override handler → type → click → waitFor result.
 * No intermediate waitForTurnComplete; the waitFor on the expected assertion is the gate.
 */

import { describe, it, expect } from "vitest";
import { render, screen, renderHook, act, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { useChatStream } from "@/lib/hooks/use-chat-stream";
import { ChatWorkspace } from "@/components/chat/ChatWorkspace";

// ── shared constants & helpers ────────────────────────────────────────────────
const enc = new TextEncoder();
const URL_CHAT = "http://localhost:3000/api/gw/v1/chat/completions";
const URL_CATALOG = "http://localhost:3000/api/gw/admin/catalog/models";
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

/** Build SSE frames for one assistant turn. */
function turnFrames(opts: {
  content?: string;
  finishReason?: string;
  usage?: { prompt: number; completion: number } | null;
}): string[] {
  const frames: string[] = [];
  frames.push(sse({ choices: [{ delta: { content: opts.content ?? "ok" } }] }));
  if (opts.finishReason) {
    frames.push(sse({ choices: [{ delta: {}, finish_reason: opts.finishReason }] }));
  }
  if (opts.usage) {
    frames.push(
      sse({
        choices: [],
        usage: {
          prompt_tokens: opts.usage.prompt,
          completion_tokens: opts.usage.completion,
          total_tokens: opts.usage.prompt + opts.usage.completion,
        },
      }),
    );
  }
  frames.push(DONE);
  return frames;
}

/** Override the catalog handler so ChatWorkspace gets per-token pricing. */
function withPricedCatalog(modelId: string, p: number, c: number) {
  server.use(
    http.get(URL_CATALOG, () =>
      HttpResponse.json({
        object: "list",
        data: [{ id: modelId, name: modelId, context_length: 128000, prompt_per_token: p, completion_per_token: c }],
      }),
    ),
  );
}

/** Override the chat handler and send one turn; returns user object. */
async function sendTurn(user: ReturnType<typeof userEvent.setup>, opts: Parameters<typeof turnFrames>[0], msg = "hi") {
  server.use(http.post(URL_CHAT, () => sseResponse(turnFrames(opts))));
  await user.type(screen.getByRole("textbox", { name: /message/i }), msg);
  await user.click(screen.getByRole("button", { name: /send/i }));
}

const readout = () => screen.getByTestId("cost-readout");

// ── hook-level: finishReason capture ─────────────────────────────────────────
describe("useChatStream — finishReason capture", () => {
  it("test_finish_reason_captured", async () => {
    server.use(
      http.post(URL_CHAT, () =>
        sseResponse(turnFrames({ content: "Hello", finishReason: "stop", usage: { prompt: 11, completion: 4 } })),
      ),
    );

    const { result } = renderHook(() => useChatStream());
    await act(async () => { result.current.send({ model: "openai/gpt-4o", text: "hi" }); });
    await waitFor(() => expect(result.current.status).toBe("idle"));

    // assistant turn is at index 1 (index 0 = user)
    const m = result.current.meta[1];
    expect(m?.finishReason).toBe("stop");
    expect(m?.usage).toEqual({ prompt_tokens: 11, completion_tokens: 4, total_tokens: 15 });
  });

  it("test_finish_reason_absent_omitted", async () => {
    server.use(
      http.post(URL_CHAT, () =>
        sseResponse(turnFrames({ content: "Hi", usage: { prompt: 5, completion: 2 } })),
      ),
    );

    const { result } = renderHook(() => useChatStream());
    await act(async () => { result.current.send({ model: "openai/gpt-4o", text: "hi" }); });
    await waitFor(() => expect(result.current.status).toBe("idle"));

    // No finish_reason frame → field must be absent
    expect(result.current.meta[1]?.finishReason).toBeUndefined();
  });
});

// ── ChatWorkspace: per-turn meta line ────────────────────────────────────────
describe("ChatWorkspace — enriched meta line", () => {
  it("test_meta_line_enriched_fields", async () => {
    withPricedCatalog("openai/gpt-4o", 0.000001, 0.000002);
    const user = userEvent.setup();
    render(<ChatWorkspace defaultModel="openai/gpt-4o" />);

    await sendTurn(user, { content: "Reply", finishReason: "stop", usage: { prompt: 11, completion: 4 } });

    // Wait until finish_reason appears in the meta line
    await waitFor(() => {
      const log = screen.getByRole("log");
      expect(log.textContent).toContain("stop");
    }, { timeout: 5000 });

    // Token breakdown must be present
    const log = screen.getByRole("log");
    expect(log.textContent).toMatch(/11p\s*\/\s*4c/);
    expect(log.textContent).toContain("15t");

    // Dollar cost from catalog pricing
    expect(log.textContent).toContain("$");
  });

  it("test_no_usage_no_cost", async () => {
    const user = userEvent.setup();
    render(<ChatWorkspace defaultModel="openai/gpt-4o" />);

    await sendTurn(user, { content: "bare", usage: null });

    // Wait for the "bare" reply to commit (no usage → session total stays at placeholder)
    await waitFor(() => {
      expect(readout()).toHaveTextContent("—");
    }, { timeout: 5000 });

    // No dollar sign anywhere in the thread
    expect(screen.getByRole("log").textContent).not.toContain("$");
  });

  it("test_no_pricemap_tokens_not_cost", async () => {
    // Default catalog has no pricing; stream sends usage
    const user = userEvent.setup();
    render(<ChatWorkspace defaultModel="openai/gpt-4o" />);

    await sendTurn(user, { content: "atok", usage: { prompt: 11, completion: 4 } });

    // Tokens must appear in the meta line
    await waitFor(() => {
      expect(screen.getByRole("log").textContent).toContain("15t");
    }, { timeout: 5000 });

    // No dollar sign (model not in priceMap)
    expect(screen.getByRole("log").textContent).not.toContain("$");
  });
});

// ── CostReadout: session cost accumulation ────────────────────────────────────
describe("ChatWorkspace — session cost in header", () => {
  it("test_session_cost_accumulates", async () => {
    // p=0.000001 $/tok, c=0.000002 $/tok
    // turn 1: 11×0.000001 + 4×0.000002 = 0.000019
    // turn 2: 5×0.000001 + 2×0.000002 = 0.000009  total = 0.000028
    withPricedCatalog("openai/gpt-4o", 0.000001, 0.000002);
    const user = userEvent.setup();
    render(<ChatWorkspace defaultModel="openai/gpt-4o" />);

    // Turn 1 — wait for the session token total to appear (proves turn 1 committed)
    await sendTurn(user, { content: "r1", usage: { prompt: 11, completion: 4 } }, "q1");
    await waitFor(() => expect(readout()).toHaveTextContent("15"), { timeout: 5000 });

    // Turn 2
    await sendTurn(user, { content: "r2", usage: { prompt: 5, completion: 2 } }, "q2");
    await waitFor(() => expect(readout()).toHaveTextContent("22"), { timeout: 5000 });

    // Session cost pill must contain "$"
    expect(readout().textContent).toContain("$");
  });

  it("test_no_cost_without_pricing", async () => {
    // Default catalog has no pricing — "$" must never appear in the cost pill
    const user = userEvent.setup();
    render(<ChatWorkspace defaultModel="openai/gpt-4o" />);

    await sendTurn(user, { content: "r", usage: { prompt: 5, completion: 2 } });

    // Tokens accumulate (honest: sessionTokens = 7)
    await waitFor(() => expect(readout()).toHaveTextContent("7"), { timeout: 5000 });

    // No dollar sign in the cost pill
    expect(readout().textContent).not.toContain("$");
  });
});
