"use client";

/**
 * lib/hooks/use-chat-stream.ts — v40 chat-workspace-page: the net-new SSE consumer.
 *
 * Owns the chat data contract (ChatMessage / Usage / StreamingState) the sibling
 * tasks import. POSTs to the streaming BFF (/api/gw/v1/chat/completions, stream:true),
 * reads response.body.getReader()+TextDecoder, accumulates choices[0].delta.content
 * live, captures the terminal usage frame, and ends on [DONE]. An AbortController
 * drives stop(): it aborts the in-flight fetch (BFF → gateway v35 disconnect-billing)
 * and COMMITS the partial text as the assistant turn. Cookie auth only (credentials:
 * "include"); never reads/writes a token client-side.
 */

import { useEffect, useRef, useState } from "react";
import { BffError, type ProblemDetail } from "@/lib/bff-client";
import { isSupported } from "@/lib/chat/param-capabilities";
import { toWireTools, type ToolCall, type ToolChoice, type ToolDef } from "@/lib/chat/tool-defs";
import {
  composeUserContent,
  type ImageAttachment,
  type MessageContentPart,
} from "@/lib/chat/attachments";

export type ChatRole = "user" | "assistant" | "system" | "tool";
/** A tool call carried on an assistant message (the OpenAI wire shape). */
export interface MessageToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}
export interface ChatMessage {
  role: ChatRole;
  /** chat-attachments: widened from string — a user turn carrying images is the
   *  OpenAI content-part array; every other turn stays a plain string (off path). */
  content: string | MessageContentPart[];
  /** assistant only — the tool calls the model requested (chat-tools-functions). */
  tool_calls?: MessageToolCall[];
  /** tool role only — the id of the assistant tool_call this message answers. */
  tool_call_id?: string;
}
export interface Usage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}
// "awaiting_tool" = the model asked to call tools and is waiting for the operator's results.
export type ChatStatus = "idle" | "streaming" | "awaiting_tool" | "error";

/**
 * Per-turn metadata, parallel to `messages` by index (so the frozen ChatMessage
 * shape is untouched). Every field is REAL or absent — never fabricated:
 *  - `at`        epoch ms the message was committed to the live thread. Absent on
 *                turns resumed via load() (we don't know when they happened).
 *  - `model`     assistant only — the model that produced the reply.
 *  - `latencyMs` assistant only — client-measured time from send() to commit.
 *  - `usage`     assistant only — the terminal usage frame (absent if upstream
 *                omitted it or the turn was stopped/failed).
 */
export interface TurnMeta {
  at?: number;
  model?: string;
  latencyMs?: number;
  usage?: Usage;
}

/** Response format selector — "text" (default) sends no key; "json_object" adds it + a hint. */
export type ResponseFormat = "text" | "json_object";

export interface SendInput {
  model: string;
  text: string;
  system?: string;
  temperature?: number;
  /** v41: opt into provider-native web-search grounding for this turn. */
  webSearch?: boolean;
  /** chat-parameters-panel: OpenAI-compatible sampling controls (pass-through; each
   *  included in the request body ONLY when set + valid — omitted-when-unset). */
  topP?: number;
  maxTokens?: number;
  frequencyPenalty?: number;
  presencePenalty?: number;
  seed?: number;
  stop?: string[];
  responseFormat?: ResponseFormat;
  /** chat-tools-functions: validated tool definitions (sent as tools[] when ≥1; omitted otherwise). */
  tools?: ToolDef[];
  toolChoice?: ToolChoice;
  /** chat-attachments: staged image attachments — when ≥1, the user turn's content
   *  becomes the OpenAI content-part array; absent/empty keeps content a plain string. */
  images?: ImageAttachment[];
}

/** Injected (merged with any user system prompt) when responseFormat === "json_object". */
const JSON_HINT = "Respond only with valid JSON.";

export interface UseChatStream {
  status: ChatStatus;
  messages: ChatMessage[];
  /** Per-turn metadata, aligned with `messages` by index (see TurnMeta). */
  meta: TurnMeta[];
  streamingText: string;
  usage?: Usage;
  error?: BffError;
  send(input: SendInput): void;
  stop(): void;
  reset(): void;
  /** v43: Replace the current thread with `messages` (resume a persisted conversation). */
  load(messages: ChatMessage[]): void;
  /** chat-tools-functions: the tool calls awaiting operator results (empty unless awaiting_tool). */
  pendingToolCalls: ToolCall[];
  /** chat-tools-functions: answer the pending calls (partial-friendly — unanswered ⇒ "") and continue. */
  submitToolResults(results: Array<{ tool_call_id: string; content: string }>): void;
}

/** A single SSE frame from the OpenAI-wire stream. */
interface SSEFrame {
  choices?: Array<{
    delta?: {
      content?: string;
      tool_calls?: Array<{
        index?: number;
        id?: string;
        type?: string;
        function?: { name?: string; arguments?: string };
      }>;
    };
    finish_reason?: string;
  }>;
  usage?: Usage;
}

/**
 * Mirror lib/bff-client appBase(): undici (Node, incl. vitest/jsdom and SSR)
 * rejects relative URLs, so use an absolute base there; in a real browser an
 * empty prefix lets window.location resolve the same-origin path.
 */
function gatewayBase(): string {
  if (typeof process !== "undefined" && process.versions?.node) {
    return process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000";
  }
  return "";
}

export interface TurnCompletePayload {
  user: string;
  assistant: string;
  usage?: Usage;
}

export function useChatStream(opts?: {
  gatewayPath?: string;
  /** v43: fired ONCE on the SUCCESS path when a non-empty assistant turn is committed. */
  onTurnComplete?: (turn: TurnCompletePayload) => void;
}): UseChatStream {
  const path = opts?.gatewayPath ?? "/api/gw/v1/chat/completions";

  const [status, setStatus] = useState<ChatStatus>("idle");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [meta, setMeta] = useState<TurnMeta[]>([]);
  const [streamingText, setStreamingText] = useState("");
  const [usage, setUsage] = useState<Usage | undefined>(undefined);
  const [error, setError] = useState<BffError | undefined>(undefined);
  // chat-tools-functions: tool calls the model requested, awaiting operator results.
  const [pendingToolCalls, setPendingToolCalls] = useState<ToolCall[]>([]);
  const pendingToolCallsRef = useRef<ToolCall[]>([]);
  // The last send() input — replayed (same tools/model/sampling/system) to continue after tool results.
  const lastInputRef = useRef<SendInput | null>(null);

  // Refs are the synchronous source of truth (state lags a render behind).
  const messagesRef = useRef<ChatMessage[]>([]);
  const metaRef = useRef<TurnMeta[]>([]);
  const partialRef = useRef("");
  const abortRef = useRef<AbortController | null>(null);
  // v43: capture the user text of the current turn so onTurnComplete can report it.
  const currentUserTextRef = useRef<string>("");
  // Per-turn meta capture: the model + the send() start time for latency.
  const currentModelRef = useRef<string>("");
  const turnStartRef = useRef<number>(0);

  // design-for-failure: unmounting mid-stream (route change / navigation) MUST
  // abort the in-flight fetch so the BFF closes and the gateway's v35
  // disconnect-billing fires — otherwise the upstream keeps running and billing
  // while the result is discarded (a silent cost leak).
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  function commitMessages(next: ChatMessage[]): void {
    messagesRef.current = next;
    setMessages(next);
  }

  function commitMeta(next: TurnMeta[]): void {
    metaRef.current = next;
    setMeta(next);
  }

  /** Append the accumulated partial as the assistant turn (if any) and go idle. */
  function finishTurn(finalUsage: Usage | undefined, isAbort: boolean): void {
    const content = partialRef.current;
    if (content) {
      commitMessages([...messagesRef.current, { role: "assistant", content }]);
      commitMeta([
        ...metaRef.current,
        {
          at: Date.now(),
          model: currentModelRef.current || undefined,
          latencyMs: turnStartRef.current ? Date.now() - turnStartRef.current : undefined,
          usage: finalUsage,
        },
      ]);
    }
    if (finalUsage) setUsage(finalUsage);
    partialRef.current = "";
    setStreamingText("");
    abortRef.current = null;
    setStatus("idle");
    // v43: fire onTurnComplete ONLY on the success path with a non-empty assistant message.
    // NEVER fires on abort or error paths.
    if (!isAbort && content && opts?.onTurnComplete) {
      opts.onTurnComplete({ user: currentUserTextRef.current, assistant: content, usage: finalUsage });
    }
  }

  /**
   * chat-tools-functions: commit a turn whose assistant message carries tool_calls (with any
   * streamed content) and enter the awaiting_tool state exposing the pending calls. Does NOT fire
   * onTurnComplete — the round is not done until the operator supplies results and continues.
   */
  function finishToolTurn(calls: ToolCall[], finalUsage: Usage | undefined): void {
    const toolCalls: MessageToolCall[] = calls.map((c) => ({
      id: c.id,
      type: "function",
      function: { name: c.name, arguments: c.arguments },
    }));
    commitMessages([
      ...messagesRef.current,
      { role: "assistant", content: partialRef.current, tool_calls: toolCalls },
    ]);
    commitMeta([
      ...metaRef.current,
      {
        at: Date.now(),
        model: currentModelRef.current || undefined,
        latencyMs: turnStartRef.current ? Date.now() - turnStartRef.current : undefined,
        usage: finalUsage,
      },
    ]);
    if (finalUsage) setUsage(finalUsage);
    partialRef.current = "";
    setStreamingText("");
    abortRef.current = null;
    pendingToolCallsRef.current = calls;
    setPendingToolCalls(calls);
    setStatus("awaiting_tool");
  }

  async function runStream(wire: ChatMessage[], input: SendInput, controller: AbortController): Promise<void> {
    try {
      const res = await fetch(`${gatewayBase()}${path}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: input.model,
          messages: wire,
          stream: true,
          stream_options: { include_usage: true },
          ...(input.temperature !== undefined ? { temperature: input.temperature } : {}),
          // v41: opt-in web-search grounding. Omitted entirely when off ⇒ body
          // byte-identical to v40 (the gateway only injects on a truthy flag).
          ...(input.webSearch ? { web_search: true } : {}),
          // chat-parameters-panel: each sampling key is included ONLY when set + valid
          // (ChatWorkspace validates before send) AND honored by the model's provider
          // (isSupported) — omitted-when-unset keeps the off/default path byte-identical,
          // and a provider-dropped param (penalties/seed on Claude etc.) never ships as a
          // silent no-op. Canonical OpenAI keys; pure pass-through (no gateway change).
          ...(input.topP !== undefined ? { top_p: input.topP } : {}),
          ...(input.maxTokens !== undefined ? { max_tokens: input.maxTokens } : {}),
          ...(input.frequencyPenalty !== undefined && isSupported(input.model, "frequencyPenalty")
            ? { frequency_penalty: input.frequencyPenalty }
            : {}),
          ...(input.presencePenalty !== undefined && isSupported(input.model, "presencePenalty")
            ? { presence_penalty: input.presencePenalty }
            : {}),
          ...(input.seed !== undefined && isSupported(input.model, "seed") ? { seed: input.seed } : {}),
          ...(input.stop && input.stop.length > 0 ? { stop: input.stop } : {}),
          ...(input.responseFormat === "json_object" && isSupported(input.model, "responseFormat")
            ? { response_format: { type: "json_object" } }
            : {}),
          // chat-tools-functions: tools[] + tool_choice ONLY when ≥1 valid tool (else BOTH absent ⇒
          // byte-identical off path). ChatWorkspace passes already-validated ToolDefs. Pass-through.
          ...(input.tools && input.tools.length > 0
            ? { tools: toWireTools(input.tools), tool_choice: input.toolChoice ?? "auto" }
            : {}),
        }),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) {
        let problem: ProblemDetail;
        try {
          problem = (await res.json()) as ProblemDetail;
        } catch {
          problem = { title: "Request failed", status: res.status };
        }
        throw new BffError(res.status, problem);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let localUsage: Usage | undefined;
      let done = false;
      // chat-tools-functions: assemble streamed tool_calls by their `index` (id + name arrive
      // once, function.arguments stream as fragments to concatenate).
      const toolAcc = new Map<number, ToolCall>();

      // stop()/reset() abort the fetch (→ BFF → gateway v35 billing), but a body
      // reader already vended from the response is not always unblocked by the
      // fetch abort (e.g. the test transport). Cancel the reader on abort so the
      // pending read() resolves and the loop can commit the partial turn.
      const onAbort = () => {
        void reader.cancel().catch(() => {});
      };
      if (controller.signal.aborted) onAbort();
      else controller.signal.addEventListener("abort", onAbort, { once: true });

      while (!done) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });

        let sep: number;
        while ((sep = buffer.indexOf("\n\n")) !== -1) {
          const frameText = buffer.slice(0, sep).trim();
          buffer = buffer.slice(sep + 2);
          if (!frameText.startsWith("data:")) continue;
          const data = frameText.slice(5).trim();
          if (data === "[DONE]") {
            done = true;
            break;
          }
          if (!data) continue;
          let frame: SSEFrame;
          try {
            frame = JSON.parse(data) as SSEFrame;
          } catch {
            continue; // ignore an unparseable frame rather than abort the stream
          }
          const choice = frame.choices?.[0];
          const delta = choice?.delta?.content;
          if (typeof delta === "string") {
            partialRef.current += delta;
            setStreamingText(partialRef.current);
          }
          // chat-tools-functions: accumulate tool_calls by index.
          const tcDeltas = choice?.delta?.tool_calls;
          if (Array.isArray(tcDeltas)) {
            for (const tc of tcDeltas) {
              const idx = tc.index ?? 0;
              const slot = toolAcc.get(idx) ?? { id: "", name: "", arguments: "" };
              if (tc.id) slot.id = tc.id;
              if (tc.function?.name) slot.name = tc.function.name;
              if (typeof tc.function?.arguments === "string") slot.arguments += tc.function.arguments;
              toolAcc.set(idx, slot);
            }
          }
          if (frame.usage) localUsage = frame.usage;
        }
      }

      controller.signal.removeEventListener("abort", onAbort);
      // chat-tools-functions: a turn that produced tool_calls is NOT a finished content turn —
      // commit the assistant tool_calls message and await operator results instead of finishTurn
      // (which would commit an empty assistant turn + fire onTurnComplete — the dropped-call bug).
      const assembled = [...toolAcc.values()].filter((t) => t.id || t.name);
      if (!controller.signal.aborted && assembled.length > 0) {
        finishToolTurn(assembled, localUsage);
      } else {
        // After the reader loop exits naturally (e.g. via reader.cancel() on abort),
        // check the signal: if aborted, treat as abort path so onTurnComplete does NOT fire.
        finishTurn(localUsage, controller.signal.aborted);
      }
    } catch (err) {
      if (controller.signal.aborted) {
        // stop() / reset() — commit whatever streamed so far, then idle.
        // isAbort=true: onTurnComplete MUST NOT fire.
        finishTurn(undefined, true);
        return;
      }
      // Genuine failure: preserve any partial as a stopped assistant turn, go error.
      if (partialRef.current) {
        commitMessages([...messagesRef.current, { role: "assistant", content: partialRef.current }]);
        commitMeta([
          ...metaRef.current,
          {
            at: Date.now(),
            model: currentModelRef.current || undefined,
            latencyMs: turnStartRef.current ? Date.now() - turnStartRef.current : undefined,
          },
        ]);
      }
      const bff =
        err instanceof BffError
          ? err
          : new BffError(0, { title: err instanceof Error ? err.message : "Stream failed", status: 0 });
      partialRef.current = "";
      setStreamingText("");
      abortRef.current = null;
      setError(bff);
      setStatus("error");
    }
  }

  /**
   * Kick off a stream for the CURRENT thread (messagesRef) with `input`. Builds the wire from the
   * thread + the system prompt (chat-parameters-panel: a JSON hint is merged when responseFormat is
   * json_object). Reused by send() (after appending the user turn) and submitToolResults() (after
   * appending the role:"tool" results) so a continuation rides the exact same params.
   */
  function startStream(input: SendInput): void {
    const jsonOn = input.responseFormat === "json_object" && isSupported(input.model, "responseFormat");
    const effectiveSystem = jsonOn
      ? input.system
        ? `${input.system}\n\n${JSON_HINT}`
        : JSON_HINT
      : input.system;
    const wire: ChatMessage[] = effectiveSystem
      ? [{ role: "system", content: effectiveSystem }, ...messagesRef.current]
      : [...messagesRef.current];

    // Per-turn meta: remember the model and start the latency clock.
    currentModelRef.current = input.model;
    turnStartRef.current = Date.now();

    const controller = new AbortController();
    abortRef.current = controller;
    partialRef.current = "";
    setStreamingText("");
    setStatus("streaming");

    void runStream(wire, input, controller);
  }

  function send(input: SendInput): void {
    const text = input.text.trim();
    if (!text || abortRef.current) return; // empty submit / already streaming = no-op

    setError(undefined);
    setUsage(undefined);
    // v43: capture user text so onTurnComplete can report it.
    currentUserTextRef.current = text;
    // chat-tools-functions: remember the input so a tool-result continuation replays it.
    lastInputRef.current = input;

    // chat-attachments: images present ⇒ content is the OpenAI content-part array;
    // none ⇒ a plain string (byte-identical off path).
    const userContent = composeUserContent(text, input.images);
    const next = [...messagesRef.current, { role: "user" as const, content: userContent }];
    commitMessages(next);
    commitMeta([...metaRef.current, { at: Date.now() }]);
    startStream(input);
  }

  /**
   * chat-tools-functions: answer the pending tool calls and continue the run. Partial-friendly —
   * EVERY pending call gets a role:"tool" message (a supplied result, else "" placeholder) so the
   * wire never leaves a tool_call unanswered (OpenAI 400s otherwise). Replays the last input's
   * tools/model/sampling/system; the continuation may itself end in tool_calls (the loop repeats).
   */
  function submitToolResults(results: Array<{ tool_call_id: string; content: string }>): void {
    const pending = pendingToolCallsRef.current;
    const input = lastInputRef.current;
    if (pending.length === 0 || abortRef.current || !input) return;

    setError(undefined);
    const byId = new Map(results.map((r) => [r.tool_call_id, r.content]));
    const toolMsgs: ChatMessage[] = pending.map((c) => ({
      role: "tool",
      content: byId.get(c.id) ?? "",
      tool_call_id: c.id,
    }));
    commitMessages([...messagesRef.current, ...toolMsgs]);
    commitMeta([...metaRef.current, ...toolMsgs.map(() => ({ at: Date.now() }) as TurnMeta)]);
    pendingToolCallsRef.current = [];
    setPendingToolCalls([]);
    startStream(input);
  }

  function stop(): void {
    abortRef.current?.abort();
  }

  function clearPendingTools(): void {
    pendingToolCallsRef.current = [];
    setPendingToolCalls([]);
  }

  function reset(): void {
    abortRef.current?.abort();
    abortRef.current = null;
    partialRef.current = "";
    messagesRef.current = [];
    setMessages([]);
    metaRef.current = [];
    setMeta([]);
    setStreamingText("");
    setUsage(undefined);
    setError(undefined);
    clearPendingTools();
    setStatus("idle");
  }

  /** v43: Replace the current thread with persisted messages (resume a conversation). */
  function load(messages: ChatMessage[]): void {
    messagesRef.current = messages;
    setMessages(messages);
    // Resumed turns carry no real timing/usage — empty meta (honest: shows "—").
    const blankMeta = messages.map(() => ({}) as TurnMeta);
    metaRef.current = blankMeta;
    setMeta(blankMeta);
    partialRef.current = "";
    setStreamingText("");
    setError(undefined);
    abortRef.current = null;
    clearPendingTools();
    setStatus("idle");
  }

  return {
    status,
    messages,
    meta,
    streamingText,
    usage,
    error,
    send,
    stop,
    reset,
    load,
    pendingToolCalls,
    submitToolResults,
  };
}
