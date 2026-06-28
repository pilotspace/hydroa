/**
 * tests-bff/chat-parameters.test.tsx — RED suite for chat-parameters-panel.
 *
 * The inspector Parameters tab gains the full OpenAI sampling set (Top P · Max
 * tokens · Frequency/Presence penalty · Seed · Stop sequences · Response format)
 * wired pass-through to POST /api/gw/v1/chat/completions. Asserts each set value
 * reaches the next request body with its canonical key; defaults stay omitted
 * (byte-identical off path); invalid values never ship; values persist across
 * turns + inspector tab switches; selecting JSON adds response_format
 * {type:"json_object"} AND a merged "Respond only with valid JSON." system hint.
 *
 * RED before Build: today the Parameters tab renders a DISABLED scaffold fieldset
 * (static ScaffoldParam spans), so the sliders/number/segmented controls don't
 * exist and SendInput carries no sampling fields — these fail until Build wires
 * the controls + the runStream body.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
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

interface ChatBody {
  model: string;
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  frequency_penalty?: number;
  presence_penalty?: number;
  seed?: number;
  stop?: string[];
  response_format?: { type: string };
  stream?: boolean;
  stream_options?: { include_usage?: boolean };
  messages: { role: string; content: string }[];
}

/** Register a chat handler that captures the LATEST POST body; returns a getter box. */
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

const setSlider = (name: RegExp, value: string) =>
  fireEvent.change(screen.getByRole("slider", { name }), { target: { value } });
const setNumber = (name: RegExp, value: string) =>
  fireEvent.change(screen.getByRole("spinbutton", { name }), { target: { value } });

async function sendHi(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByRole("textbox", { name: /message/i }), "hi");
  await user.click(screen.getByRole("button", { name: /send/i }));
}

describe("chat-parameters-panel", () => {
  it("test_top_p_reaches_body", async () => {
    const box = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    setSlider(/top p/i, "0.5");
    await sendHi(user);

    await waitFor(() => expect(box.body?.top_p).toBe(0.5));
    // the streaming seam is otherwise unchanged
    expect(box.body?.stream).toBe(true);
    expect(box.body?.stream_options?.include_usage).toBe(true);
  });

  it("test_canonical_keys_map", async () => {
    const box = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    setNumber(/max tokens/i, "256");
    setSlider(/frequency penalty/i, "0.5");
    setSlider(/presence penalty/i, "-0.5");
    setNumber(/seed/i, "42");
    await user.type(screen.getByRole("textbox", { name: /stop sequences/i }), "END");
    await user.keyboard("{Enter}");
    await sendHi(user);

    await waitFor(() => expect(box.body?.max_tokens).toBe(256));
    expect(box.body?.frequency_penalty).toBe(0.5);
    expect(box.body?.presence_penalty).toBe(-0.5);
    expect(box.body?.seed).toBe(42);
    expect(box.body?.stop).toEqual(["END"]);
  });

  it("test_response_format_json_sends_key_and_hint", async () => {
    const box = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await user.click(screen.getByRole("button", { name: /^json$/i }));
    await sendHi(user);
    await waitFor(() => expect(box.body?.response_format).toEqual({ type: "json_object" }));
    expect(box.body?.messages.some((m) => m.role === "system" && /respond only with valid json/i.test(m.content))).toBe(true);

    // switch back to Text → next run drops both the key and the hint
    await user.click(screen.getByRole("button", { name: /^text$/i }));
    await sendHi(user);
    await waitFor(() => expect("response_format" in (box.body ?? {})).toBe(false));
    expect(box.body?.messages.some((m) => m.role === "system")).toBe(false);
  });

  it("test_json_hint_merges_with_user_system", async () => {
    const box = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: /system prompt/i }), "Be terse");
    await user.click(screen.getByRole("button", { name: /^json$/i }));
    await sendHi(user);

    await waitFor(() =>
      expect(box.body?.messages[0]).toEqual({
        role: "system",
        content: "Be terse\n\nRespond only with valid JSON.",
      }),
    );
  });

  it("test_values_persist_across_turns_and_tabs", async () => {
    const box = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    setSlider(/top p/i, "0.3");
    await sendHi(user);
    await waitFor(() => expect(box.body?.top_p).toBe(0.3));

    // switch to Tools, back to Parameters — the value survives
    await user.click(screen.getByRole("tab", { name: /tools/i }));
    await user.click(screen.getByRole("tab", { name: /parameters/i }));
    expect(screen.getByRole("slider", { name: /top p/i })).toHaveValue("0.3");

    await user.type(screen.getByRole("textbox", { name: /message/i }), "again");
    await user.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(box.body?.top_p).toBe(0.3));
  });

  it("test_defaults_omitted_byte_identical", async () => {
    const box = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    await sendHi(user);

    await waitFor(() => expect(box.body?.model).toBe("openai/gpt-4o"));
    const b = box.body ?? ({} as ChatBody);
    for (const k of ["top_p", "max_tokens", "stop", "frequency_penalty", "presence_penalty", "seed", "response_format"]) {
      expect(k in b).toBe(false);
    }
    // the v40/v41 shape is intact
    expect(b.stream).toBe(true);
    expect(b.stream_options?.include_usage).toBe(true);
    expect(b.messages.at(-1)?.role).toBe("user");
  });

  it("test_default_does_not_leak", async () => {
    const box = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);
    // Top P untouched → no key
    await sendHi(user);
    await waitFor(() => expect(box.body?.model).toBe("openai/gpt-4o"));
    expect("top_p" in (box.body ?? {})).toBe(false);
  });

  it("test_out_of_range_clamped_or_omitted", async () => {
    const box = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    setNumber(/max tokens/i, "0"); // below min 1 → invalid → omitted
    // sliders driven out of range programmatically must be CLAMPED before the wire
    // (HTML min/max only bounds a pointer drag) — the §4-named "drive Top P > 1" case.
    setSlider(/top p/i, "5"); // → clamp to max 1
    setSlider(/frequency penalty/i, "3"); // → clamp to max 2
    await sendHi(user);

    await waitFor(() => expect(box.body?.model).toBe("openai/gpt-4o"));
    // a malformed value is never emitted as a key
    expect(box.body?.max_tokens).not.toBe(0);
    expect("max_tokens" in (box.body ?? {})).toBe(false);
    // out-of-range slider values are clamped, NEVER shipped raw
    expect(box.body?.top_p).not.toBe(5);
    expect(box.body?.top_p ?? 0).toBeLessThanOrEqual(1);
    expect(box.body?.frequency_penalty).not.toBe(3);
    expect(box.body?.frequency_penalty ?? 0).toBeLessThanOrEqual(2);
    // no empty stop string ever ships
    expect(box.body?.stop ?? []).not.toContain("");
  });

  it("test_set_value_reaches_wire_seed", async () => {
    const box = captureChat();
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    setNumber(/seed/i, "7");
    await sendHi(user);

    await waitFor(() => expect(box.body?.seed).toBe(7));
  });

  it("test_unsupported_param_gated_on_provider_switch", async () => {
    const box = captureChat();
    // offer a Claude model so the picker can switch providers
    server.use(
      http.get("http://localhost:3000/api/gw/admin/catalog/models", () =>
        HttpResponse.json({
          object: "list",
          data: [{ id: "openai/gpt-4o" }, { id: "anthropic/claude-3.5-sonnet" }],
        }),
      ),
    );
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    // set one supported (top_p) + one unsupported-on-Claude (seed) param while on OpenAI
    setSlider(/top p/i, "0.4");
    setNumber(/seed/i, "7");

    // switch the model to Anthropic
    const picker = await screen.findByRole("combobox", { name: /model/i });
    await waitFor(() =>
      expect(within(picker).queryByText("anthropic/claude-3.5-sonnet")).not.toBeNull(),
    );
    await user.selectOptions(picker, "anthropic/claude-3.5-sonnet");

    // the provider-unsupported controls are disabled + annotated
    expect(screen.getByRole("spinbutton", { name: /seed/i })).toBeDisabled();
    expect(screen.getByRole("slider", { name: /frequency penalty/i })).toBeDisabled();
    expect(screen.getByRole("slider", { name: /presence penalty/i })).toBeDisabled();
    expect(screen.getAllByText(/ignored by anthropic/i).length).toBeGreaterThanOrEqual(3);
    // a supported control stays live
    expect(screen.getByRole("slider", { name: /top p/i })).not.toBeDisabled();

    await sendHi(user);

    await waitFor(() => expect(box.body?.model).toBe("anthropic/claude-3.5-sonnet"));
    // the gateway would silently drop these → the UI omits them entirely
    expect("seed" in (box.body ?? {})).toBe(false);
    expect("frequency_penalty" in (box.body ?? {})).toBe(false);
    expect("presence_penalty" in (box.body ?? {})).toBe(false);
    // the supported param still ships
    expect(box.body?.top_p).toBe(0.4);
  });

  it("test_bedrock_suppresses_response_format_and_json_hint", async () => {
    const box = captureChat();
    server.use(
      http.get("http://localhost:3000/api/gw/admin/catalog/models", () =>
        HttpResponse.json({
          object: "list",
          data: [{ id: "openai/gpt-4o" }, { id: "bedrock/anthropic.claude-3" }],
        }),
      ),
    );
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    // user system prompt + JSON format while on OpenAI (where both are honored)
    await user.type(screen.getByRole("textbox", { name: /system prompt/i }), "Be terse");
    await user.click(screen.getByRole("button", { name: /^json$/i }));

    // switch to Bedrock — Response format is the ONE extra param Bedrock drops
    const picker = await screen.findByRole("combobox", { name: /model/i });
    await waitFor(() =>
      expect(within(picker).queryByText("bedrock/anthropic.claude-3")).not.toBeNull(),
    );
    await user.selectOptions(picker, "bedrock/anthropic.claude-3");

    expect(screen.getByRole("button", { name: /^json$/i })).toBeDisabled();
    expect(screen.getAllByText(/ignored by bedrock/i).length).toBeGreaterThanOrEqual(1);

    await sendHi(user);

    await waitFor(() => expect(box.body?.model).toBe("bedrock/anthropic.claude-3"));
    // both the key AND the JSON hint are suppressed; the user system prompt survives clean
    expect("response_format" in (box.body ?? {})).toBe(false);
    expect(box.body?.messages[0]).toEqual({ role: "system", content: "Be terse" });
    expect(box.body?.messages.some((m) => /respond only with valid json/i.test(m.content))).toBe(false);
  });
});
