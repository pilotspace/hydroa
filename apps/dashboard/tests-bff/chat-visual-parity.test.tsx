/**
 * tests-bff/chat-visual-parity.test.tsx — chat full-mockup-parity (presentational layer).
 *
 * The design prototype (chat-workspace-page) shows message identity (avatars +
 * role labels), a "Today" day divider, per-turn actions (Copy / Regenerate), and
 * a richer composer (quick-action chips, a live token estimate, a footer hint).
 * This pins the honest subset — everything that maps to REAL data (the current
 * user's initials, the message content, the input text) without fabricating the
 * per-turn latency/cost the frozen SSE seam does not carry.
 *
 * Runs in the "bff" project (jsdom origin http://localhost:3000): MSW intercepts
 * both /api/auth/me (avatar initials) and the streaming chat POST.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { ChatWorkspace } from "@/components/chat/ChatWorkspace";

const enc = new TextEncoder();
const URL_CHAT = "http://localhost:3000/api/gw/v1/chat/completions";
const sse = (o: unknown) => `data: ${JSON.stringify(o)}\n\n`;
const DONE = "data: [DONE]\n\n";

function streamOf(parts: string[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(c) {
      for (const p of parts) c.enqueue(enc.encode(p));
      c.close();
    },
  });
}
function sseResponse(parts: string[]): HttpResponse {
  return new HttpResponse(streamOf(parts), {
    headers: { "Content-Type": "text/event-stream" },
  });
}
const REPLY = [
  sse({ choices: [{ delta: { role: "assistant" } }] }),
  sse({ choices: [{ delta: { content: "Hi there" } }] }),
  sse({ choices: [], usage: { prompt_tokens: 5, completion_tokens: 2, total_tokens: 7 } }),
  DONE,
];

async function sendOneTurn() {
  const user = userEvent.setup();
  render(<ChatWorkspace />);
  const box = await screen.findByLabelText("Message");
  await user.type(box, "hello");
  await user.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(screen.getByText("Hi there")).toBeInTheDocument());
  return user;
}

describe("chat visual parity", () => {
  it("test_today_divider_and_user_initials", async () => {
    server.use(http.post(URL_CHAT, () => sseResponse(REPLY)));
    await sendOneTurn();
    // a day divider groups the thread
    expect(screen.getByText(/today/i)).toBeInTheDocument();
    // the user avatar shows real initials derived from /api/auth/me (ada@acme.io -> AD)
    expect(await screen.findByText("AD")).toBeInTheDocument();
  });

  it("test_assistant_turn_exposes_copy_and_regenerate", async () => {
    server.use(http.post(URL_CHAT, () => sseResponse(REPLY)));
    await sendOneTurn();
    const turn = screen.getByText("Hi there").closest("[data-role='assistant']");
    expect(turn).not.toBeNull();
    const t = within(turn as HTMLElement);
    expect(t.getByRole("button", { name: /copy/i })).toBeInTheDocument();
    expect(t.getByRole("button", { name: /regenerate/i })).toBeInTheDocument();
  });

  it("test_regenerate_reissues_a_chat_request", async () => {
    let calls = 0;
    server.use(
      http.post(URL_CHAT, () => {
        calls += 1;
        return sseResponse(REPLY);
      }),
    );
    const user = await sendOneTurn();
    expect(calls).toBe(1);
    const turn = screen.getByText("Hi there").closest("[data-role='assistant']") as HTMLElement;
    await user.click(within(turn).getByRole("button", { name: /regenerate/i }));
    await waitFor(() => expect(calls).toBe(2));
  });

  it("test_quick_action_chip_prefills_the_composer", async () => {
    const user = userEvent.setup();
    render(<ChatWorkspace />);
    const chip = await screen.findByRole("button", { name: /summarize/i });
    await user.click(chip);
    const box = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;
    expect(box.value.toLowerCase()).toContain("summarize");
  });

  it("test_composer_shows_footer_hint_and_token_estimate", async () => {
    const user = userEvent.setup();
    render(<ChatWorkspace />);
    // footer hint about Enter-to-send
    expect(screen.getByText(/shift/i)).toBeInTheDocument();
    // a live token estimate appears as the user types
    const box = await screen.findByLabelText("Message");
    await user.type(box, "some words here to estimate");
    expect(screen.getByText(/tokens?/i)).toBeInTheDocument();
  });
});
