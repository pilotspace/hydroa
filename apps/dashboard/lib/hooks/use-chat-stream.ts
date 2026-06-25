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

export type ChatRole = "user" | "assistant" | "system";
export interface ChatMessage {
  role: ChatRole;
  content: string;
}
export interface Usage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}
export type ChatStatus = "idle" | "streaming" | "error";

export interface SendInput {
  model: string;
  text: string;
  system?: string;
  temperature?: number;
  /** v41: opt into provider-native web-search grounding for this turn. */
  webSearch?: boolean;
}

export interface UseChatStream {
  status: ChatStatus;
  messages: ChatMessage[];
  streamingText: string;
  usage?: Usage;
  error?: BffError;
  send(input: SendInput): void;
  stop(): void;
  reset(): void;
  /** v43: Replace the current thread with `messages` (resume a persisted conversation). */
  load(messages: ChatMessage[]): void;
}

/** A single SSE frame from the OpenAI-wire stream. */
interface SSEFrame {
  choices?: Array<{ delta?: { content?: string } }>;
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
  const [streamingText, setStreamingText] = useState("");
  const [usage, setUsage] = useState<Usage | undefined>(undefined);
  const [error, setError] = useState<BffError | undefined>(undefined);

  // Refs are the synchronous source of truth (state lags a render behind).
  const messagesRef = useRef<ChatMessage[]>([]);
  const partialRef = useRef("");
  const abortRef = useRef<AbortController | null>(null);
  // v43: capture the user text of the current turn so onTurnComplete can report it.
  const currentUserTextRef = useRef<string>("");

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

  /** Append the accumulated partial as the assistant turn (if any) and go idle. */
  function finishTurn(finalUsage: Usage | undefined, isAbort: boolean): void {
    const content = partialRef.current;
    if (content) commitMessages([...messagesRef.current, { role: "assistant", content }]);
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
          const delta = frame.choices?.[0]?.delta?.content;
          if (typeof delta === "string") {
            partialRef.current += delta;
            setStreamingText(partialRef.current);
          }
          if (frame.usage) localUsage = frame.usage;
        }
      }

      controller.signal.removeEventListener("abort", onAbort);
      // After the reader loop exits naturally (e.g. via reader.cancel() on abort),
      // check the signal: if aborted, treat as abort path so onTurnComplete does NOT fire.
      finishTurn(localUsage, controller.signal.aborted);
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

  function send(input: SendInput): void {
    const text = input.text.trim();
    if (!text || abortRef.current) return; // empty submit / already streaming = no-op

    setError(undefined);
    setUsage(undefined);
    // v43: capture user text so onTurnComplete can report it.
    currentUserTextRef.current = text;

    const next = [...messagesRef.current, { role: "user" as const, content: text }];
    commitMessages(next);
    const wire: ChatMessage[] = input.system
      ? [{ role: "system", content: input.system }, ...next]
      : next;

    const controller = new AbortController();
    abortRef.current = controller;
    partialRef.current = "";
    setStreamingText("");
    setStatus("streaming");

    void runStream(wire, input, controller);
  }

  function stop(): void {
    abortRef.current?.abort();
  }

  function reset(): void {
    abortRef.current?.abort();
    abortRef.current = null;
    partialRef.current = "";
    messagesRef.current = [];
    setMessages([]);
    setStreamingText("");
    setUsage(undefined);
    setError(undefined);
    setStatus("idle");
  }

  /** v43: Replace the current thread with persisted messages (resume a conversation). */
  function load(messages: ChatMessage[]): void {
    messagesRef.current = messages;
    setMessages(messages);
    partialRef.current = "";
    setStreamingText("");
    setError(undefined);
    abortRef.current = null;
    setStatus("idle");
  }

  return { status, messages, streamingText, usage, error, send, stop, reset, load };
}
