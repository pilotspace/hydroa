/**
 * tests-bff/chat-playground-shell.test.tsx — RED suite for chat-playground-shell.
 *
 * Pins the frozen §3 structural contract: the /app/chat surface becomes a 3-pane
 * Console playground — a SESSIONS rail, a CONVERSATION column (role=log thread +
 * top bar + composer), and a PARAMETERS/INSPECTOR panel (Parameters · Tools · Code
 * tabs). The existing model controls (System prompt / Temperature / Web search)
 * relocate into the inspector's Parameters tab; every preserved chat seam survives
 * the reshape (relocation only, never weakened).
 *
 * RED before Build: today's ChatWorkspace is a 2-pane sidebar+main with the
 * controls behind a composer "Model settings" disclosure — there is no inspector
 * tablist, so the three-pane / Parameters-tab assertions fail until Build reshapes
 * the layout. Runs in the BFF project (jsdom origin http://localhost:3000) so MSW
 * intercepts the streaming chat POST + catalog + auth reads.
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

function sseResponse(parts: string[], close = true): HttpResponse {
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      for (const p of parts) c.enqueue(enc.encode(p));
      if (close) c.close();
    },
  });
  return new HttpResponse(stream, { headers: { "Content-Type": "text/event-stream" } });
}

const REPLY = [
  sse({ choices: [{ delta: { role: "assistant" } }] }),
  sse({ choices: [{ delta: { content: "Hi there" } }] }),
  sse({ choices: [], usage: { prompt_tokens: 5, completion_tokens: 2, total_tokens: 7 } }),
  DONE,
];

/** The Parameters tab is the inspector's default; this is a safe no-op once it is. */
async function openParameters(user: ReturnType<typeof userEvent.setup>) {
  const tab = screen.queryByRole("tab", { name: /parameters/i });
  if (tab && tab.getAttribute("aria-selected") !== "true") await user.click(tab);
}

async function sendHi(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByRole("textbox", { name: /message/i }), "hi");
  await user.click(screen.getByRole("button", { name: /send/i }));
}

describe("chat-playground-shell — the 3-pane Console structure", () => {
  it("test_three_pane_layout_renders", () => {
    render(<ChatWorkspace />);
    // SESSIONS rail (the restyled ChatHistorySidebar keeps its landmark name)
    expect(screen.getByRole("complementary", { name: /conversation history/i })).toBeInTheDocument();
    // CONVERSATION column — the thread is a role=log live region
    expect(screen.getByRole("log", { name: /conversation/i })).toBeInTheDocument();
    // INSPECTOR panel — a tablist whose default tab is Parameters
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /parameters/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /tools/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /code/i })).toBeInTheDocument();
    // exactly one h1
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("test_thread_is_log_with_data_role", async () => {
    let postedUrl: string | undefined;
    server.use(
      http.post(URL_CHAT, ({ request }) => {
        postedUrl = request.url;
        return sseResponse(REPLY);
      }),
    );
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    const log = screen.getByRole("log", { name: /conversation/i });
    expect(log).toHaveAttribute("aria-live", "polite");

    await sendHi(user);
    await waitFor(() => expect(screen.getByText("Hi there")).toBeInTheDocument());

    // turns carry data-role; the assistant turn is a direct descendant of the log
    const assistantTurn = screen.getByText("Hi there").closest("[data-role='assistant']");
    expect(assistantTurn).not.toBeNull();
    expect(log).toContainElement(assistantTurn as HTMLElement);
    // the streaming seam is unchanged
    expect(postedUrl).toBe(URL_CHAT);
  });

  it("test_controls_in_inspector_parameters", async () => {
    const user = userEvent.setup();
    render(<ChatWorkspace />);
    await openParameters(user);

    // the three relocated controls live in the inspector, aria-labels unchanged
    const sys = screen.getByRole("textbox", { name: /system prompt/i });
    const temp = screen.getByRole("slider", { name: /temperature/i });
    const web = screen.getByRole("checkbox", { name: /web search/i });
    expect(sys).toBeInTheDocument();
    expect(temp).toBeInTheDocument();
    expect(web).toBeInTheDocument();

    // functional after the move
    await user.type(sys, "Be terse");
    expect(sys).toHaveValue("Be terse");
    await user.click(web);
    expect(web).toBeChecked();
  });

  it("test_topbar_model_and_cost", () => {
    render(<ChatWorkspace />);
    // the conversation top bar holds the model selector + the running-cost chip
    expect(screen.getByRole("combobox", { name: /model/i })).toBeInTheDocument();
    const cost = screen.getByTestId("cost-readout");
    expect(cost).toBeInTheDocument();
    // honest placeholder before any usage (computed exactly as before — token-only)
    expect(cost).toHaveTextContent("—");
  });

  it("test_four_states", async () => {
    const user = userEvent.setup();

    // 1) EMPTY — starter affordances on mount
    const { unmount } = render(<ChatWorkspace />);
    expect(screen.getByText(/start a conversation/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /summarize/i })).toBeInTheDocument();

    // 2) STREAMING — a never-closing stream keeps the Stop control visible
    server.use(http.post(URL_CHAT, () => sseResponse([sse({ choices: [{ delta: { content: "…" } }] })], false)));
    await sendHi(user);
    expect(await screen.findByRole("button", { name: /stop/i })).toBeInTheDocument();
    unmount();

    // 3) ERROR — a settled 4xx surfaces a role=alert
    server.use(http.post(URL_CHAT, () => HttpResponse.json({ title: "Budget exceeded", status: 402 }, { status: 402 })));
    const user2 = userEvent.setup();
    render(<ChatWorkspace />);
    await sendHi(user2);
    expect(await screen.findByRole("alert")).toBeInTheDocument();

    // 4) SUCCESS — a populated thread
    server.use(http.post(URL_CHAT, () => sseResponse(REPLY)));
    const user3 = userEvent.setup();
    render(<ChatWorkspace />);
    await user3.type(screen.getAllByRole("textbox", { name: /message/i })[0], "ok");
    await user3.click(screen.getAllByRole("button", { name: /send/i })[0]);
    await waitFor(() => expect(screen.getAllByText("Hi there").length).toBeGreaterThan(0));
  });

  it("test_seam_not_broken", async () => {
    server.use(http.post(URL_CHAT, () => sseResponse(REPLY)));
    const user = userEvent.setup();
    const { container } = render(<ChatWorkspace />);
    await openParameters(user);

    // the frozen hook union is byte-present after the reshape (relocation only)
    expect(screen.getByRole("complementary", { name: /conversation history/i })).toBeInTheDocument();
    expect(screen.getByRole("log", { name: /conversation/i })).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /send/i })).toBeInTheDocument();
    expect(screen.getByLabelText("System prompt")).toBeInTheDocument();
    expect(screen.getByLabelText("Temperature")).toBeInTheDocument();
    expect(screen.getByLabelText("Web search")).toBeInTheDocument();
    expect(screen.getByTestId("cost-readout")).toBeInTheDocument();
    expect(container.querySelector('[data-slot="model-picker"]')).not.toBeNull();
    expect(container.querySelector('[data-slot="session-cost"]')).not.toBeNull();

    // after a stream: data-role turns + per-turn Copy / Regenerate survive
    await sendHi(user);
    const turn = (await screen.findByText("Hi there")).closest("[data-role='assistant']") as HTMLElement;
    expect(turn).not.toBeNull();
    expect(within(turn).getByRole("button", { name: /copy/i })).toBeInTheDocument();
    expect(within(turn).getByRole("button", { name: /regenerate/i })).toBeInTheDocument();
  });

  it("test_no_control_lost", async () => {
    const user = userEvent.setup();
    render(<ChatWorkspace />);
    await openParameters(user);
    // all three pre-existing controls are still present (none dropped in the move)
    expect(screen.getByRole("textbox", { name: /system prompt/i })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: /temperature/i })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /web search/i })).toBeInTheDocument();
  });
});
