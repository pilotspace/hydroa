/**
 * tests-bff/chat-cost-readout.test.tsx — RED suite for v40 chat-cost-readout.
 *
 * The header cost slot becomes live: a session token total that accumulates per
 * turn + the latest turn's tokens, honestly (token counts only, never a $). RED
 * before Build: the slot is a static "Session cost —" with no testid and no
 * accumulation, so these fail until CostReadout + the ChatWorkspace accumulation
 * land.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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

type Tokens = { prompt: number; completion: number } | null;

/** Stream one assistant turn ("ok"); when tokens is null, omit the usage frame. */
async function streamTurn(user: ReturnType<typeof userEvent.setup>, tokens: Tokens) {
  const frames = [sse({ choices: [{ delta: { content: "ok" } }] })];
  if (tokens) {
    frames.push(
      sse({
        choices: [],
        usage: {
          prompt_tokens: tokens.prompt,
          completion_tokens: tokens.completion,
          total_tokens: tokens.prompt + tokens.completion,
        },
      }),
    );
  }
  frames.push(DONE);
  server.use(http.post(URL_CHAT, () => sseResponse(frames)));
  await user.type(screen.getByRole("textbox", { name: /message/i }), "hi");
  await user.click(screen.getByRole("button", { name: /send/i }));
}

const readout = () => screen.getByTestId("cost-readout");

describe("chat-cost-readout", () => {
  it("test_session_total_accumulates", async () => {
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await streamTurn(user, { prompt: 4, completion: 3 }); // 7
    await waitFor(() => expect(readout()).toHaveTextContent("7"));
    await streamTurn(user, { prompt: 10, completion: 5 }); // +15 = 22
    await waitFor(() => expect(readout()).toHaveTextContent("22"));
  });

  it("test_latest_turn_tokens_shown", async () => {
    const user = userEvent.setup();
    render(<ChatWorkspace />);
    await streamTurn(user, { prompt: 5, completion: 2 }); // total 7, last 7
    await waitFor(() => expect(readout()).toHaveTextContent(/last\s*7/i));
  });

  it("test_placeholder_before_usage", () => {
    render(<ChatWorkspace />);
    expect(readout()).toHaveTextContent("—");
    expect(readout().textContent ?? "").not.toMatch(/\d/);
  });

  it("test_no_dollar_sign", async () => {
    const user = userEvent.setup();
    render(<ChatWorkspace />);
    await streamTurn(user, { prompt: 5, completion: 2 });
    await waitFor(() => expect(readout()).toHaveTextContent("7"));
    expect(readout().textContent ?? "").not.toContain("$");
  });

  it("test_usageless_turn_no_change", async () => {
    const user = userEvent.setup();
    render(<ChatWorkspace />);
    await streamTurn(user, { prompt: 4, completion: 3 }); // 7
    await waitFor(() => expect(readout()).toHaveTextContent("7"));

    await streamTurn(user, null); // no usage frame
    await waitFor(() => expect(screen.getAllByText("ok")).toHaveLength(2)); // both turns committed
    expect(readout()).toHaveTextContent("7");
    expect(readout().textContent ?? "").not.toContain("22");
  });

  it("test_rerender_no_double_count", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<ChatWorkspace />);
    await streamTurn(user, { prompt: 4, completion: 3 }); // 7
    await waitFor(() => expect(readout()).toHaveTextContent("7"));

    rerender(<ChatWorkspace />);
    expect(readout()).toHaveTextContent("7"); // not 14
  });
});
