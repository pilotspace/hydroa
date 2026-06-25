"use client";

/**
 * components/chat/ChatWorkspace.tsx — v40 chat-workspace-page.
 *
 * The /app/chat surface: a header (with cost + model-picker SLOTS the sibling
 * tasks fill), a scrollable multi-turn thread rendering the four UI states
 * (empty / streaming / error / success), and a composer whose Send toggles to
 * Stop while a turn streams. All streaming behaviour lives in useChatStream;
 * this component is presentation + input. Build target: the confirmed capture
 * .add/design/captures/chat-workspace-page.png.
 */

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Send, Square } from "lucide-react";
import { useChatStream, type ChatMessage, type Usage } from "@/lib/hooks/use-chat-stream";
import { ModelPicker } from "@/components/chat/ModelPicker";
import { ModelControls } from "@/components/chat/ModelControls";
import { CostReadout } from "@/components/chat/CostReadout";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Empty, ErrorState } from "@/components/ui/states";
import { cn } from "@/lib/cn";

const DEFAULT_MODEL = "openai/gpt-4o";

export interface ChatWorkspaceProps {
  /** Default model until chat-model-controls supplies a live picker. */
  defaultModel?: string;
}

export function ChatWorkspace({ defaultModel = DEFAULT_MODEL }: ChatWorkspaceProps) {
  const { status, messages, streamingText, usage, error, send, stop } = useChatStream();
  const [input, setInput] = useState("");
  const [model, setModel] = useState(defaultModel);
  const [system, setSystem] = useState("");
  const [temperature, setTemperature] = useState(1);
  const [sessionTokens, setSessionTokens] = useState(0);
  const countedRef = useRef<Usage | undefined>(undefined);
  const threadEndRef = useRef<HTMLDivElement>(null);

  const isStreaming = status === "streaming";

  // Accumulate the session token total — each completed turn's usage object is
  // counted EXACTLY once (the identity guard survives StrictMode double-invoke
  // and any re-render where `usage` is unchanged).
  useEffect(() => {
    if (usage && usage !== countedRef.current) {
      countedRef.current = usage;
      setSessionTokens((t) => t + usage.total_tokens);
    }
  }, [usage]);

  // Scroll-to-latest as the thread grows (design: scroll-to-latest affordance).
  // scrollIntoView is absent in jsdom and older engines — guard before calling.
  useEffect(() => {
    const el = threadEndRef.current;
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [messages, streamingText]);

  function submit() {
    const text = input.trim();
    if (!text || isStreaming) return; // empty submit / mid-stream = no-op
    send({ model, text, system: system.trim() || undefined, temperature });
    setInput("");
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit();
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault(); // Enter sends; Shift+Enter inserts a newline
      submit();
    }
  }

  const showEmpty = messages.length === 0 && !isStreaming && status !== "error";

  return (
    <div className="flex h-full min-h-0 flex-col bg-muted/30">
      <header className="flex items-center justify-between border-b border-border bg-background px-6 py-3">
        <h1 className="text-lg font-semibold text-foreground">Chat</h1>
        <div className="flex items-center gap-3">
          {/* chat-cost-readout — live session token total + latest turn (tokens only). */}
          <CostReadout sessionTokens={sessionTokens} lastTurn={usage} />
          {/* SLOT: chat-model-controls (picker) */}
          <span data-slot="model-picker">
            <ModelPicker value={model} onChange={setModel} />
          </span>
        </div>
      </header>

      <div
        className="min-h-0 flex-1 overflow-y-auto px-4 py-6"
        role="log"
        aria-live="polite"
        aria-label="Conversation"
      >
        <div className="mx-auto flex max-w-3xl flex-col gap-4">
          {showEmpty ? (
            <Empty
              title="Start a conversation"
              description={`Message ${model} to begin — replies stream in live.`}
            />
          ) : null}

          {messages.map((m, i) => (
            <MessageBubble key={i} message={m} />
          ))}

          {isStreaming ? (
            <MessageBubble message={{ role: "assistant", content: streamingText }} streaming />
          ) : null}

          {status === "error" && error ? (
            <ErrorState
              title={error.problem.title ?? "Request failed"}
              description={error.status ? `HTTP ${error.status}` : undefined}
            />
          ) : null}

          <div ref={threadEndRef} />
        </div>
      </div>

      <form onSubmit={onSubmit} className="border-t border-border bg-background px-4 py-3">
        <div className="mx-auto flex max-w-3xl flex-col gap-2">
          {/* SLOT: chat-model-controls (system + temperature) — collapsed by default */}
          <ModelControls
            system={system}
            onSystemChange={setSystem}
            temperature={temperature}
            onTemperatureChange={setTemperature}
          />
          <div className="flex items-end gap-2 rounded-xl border border-border bg-background p-2 focus-within:ring-2 focus-within:ring-ring">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={`Message ${model}…`}
            aria-label="Message"
            rows={1}
            className="min-h-9 resize-none border-0 shadow-none focus-visible:ring-0"
          />
          {isStreaming ? (
            <Button type="button" variant="outline" onClick={stop} aria-label="Stop">
              <Square className="size-4" aria-hidden="true" />
              Stop
            </Button>
          ) : (
            <Button type="submit" aria-label="Send">
              <Send className="size-4" aria-hidden="true" />
              Send
            </Button>
          )}
          </div>
        </div>
      </form>
    </div>
  );
}

function MessageBubble({ message, streaming = false }: { message: ChatMessage; streaming?: boolean }) {
  const isUser = message.role === "user";
  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm",
          isUser
            ? "bg-primary text-primary-foreground"
            : "border border-border bg-background text-foreground",
        )}
      >
        {message.content}
        {streaming ? (
          <span
            className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-current align-middle"
            aria-hidden="true"
          />
        ) : null}
      </div>
    </div>
  );
}
