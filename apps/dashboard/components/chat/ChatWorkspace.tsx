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

import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Send, Square } from "lucide-react";
import { useChatStream, type ChatMessage, type Usage } from "@/lib/hooks/use-chat-stream";
import { ChatHistorySidebar } from "@/components/chat/ChatHistorySidebar";
import { ModelPicker } from "@/components/chat/ModelPicker";
import { MessageMarkdown } from "@/components/chat/MessageMarkdown";
import { ModelControls } from "@/components/chat/ModelControls";
import { CostReadout } from "@/components/chat/CostReadout";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Empty, ErrorState } from "@/components/ui/states";
import { cn } from "@/lib/cn";
import {
  createConversation,
  getConversation,
  appendMessage,
} from "@/lib/conversations";

const DEFAULT_MODEL = "openai/gpt-4o";

/** First ~40 chars of text, trimmed — used as a conversation title slug. */
function slug(text: string): string {
  return text.trim().slice(0, 40).trim();
}

export interface ChatWorkspaceProps {
  /** Default model until chat-model-controls supplies a live picker. */
  defaultModel?: string;
}

export function ChatWorkspace({ defaultModel = DEFAULT_MODEL }: ChatWorkspaceProps) {
  // v43: conversation identity + sidebar refresh trigger
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  // Ref to the resolved conversation id (set synchronously on resume/new, or set
  // after the createConversation promise resolves on the first turn).
  const activeConvIdRef = useRef<string | null>(null);
  // On the first turn we fire createConversation concurrently with the stream.
  // onTurnComplete must await this promise so it appends to the right id even
  // when the stream finishes before the create call resolves.
  const pendingConvIdRef = useRef<Promise<string | null> | null>(null);

  const onTurnComplete = useCallback(
    async ({ assistant }: { user: string; assistant: string }) => {
      // Resolve the conversation id — may be set already or still inflight.
      let id = activeConvIdRef.current;
      if (!id && pendingConvIdRef.current) {
        id = await pendingConvIdRef.current;
      }
      if (!id) return;
      try {
        await appendMessage(id, "assistant", assistant);
      } catch {
        // best-effort: swallow — the on-screen turn is already visible
      }
      setRefreshKey((k) => k + 1);
    },
    [],
  );

  const { status, messages, streamingText, usage, error, send, stop, reset, load } =
    useChatStream({ onTurnComplete });

  const [input, setInput] = useState("");
  const [model, setModel] = useState(defaultModel);
  const [system, setSystem] = useState("");
  const [temperature, setTemperature] = useState(1);
  const [webSearch, setWebSearch] = useState(false);
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

    // v43: best-effort persistence — create conversation on first turn, append user msg.
    // Store a promise so onTurnComplete can await the conv id even if the stream
    // resolves before the createConversation call does.
    const persistPromise: Promise<string | null> = (async () => {
      try {
        let id = activeConvIdRef.current;
        if (!id) {
          const conv = await createConversation(slug(text));
          id = conv.id;
          activeConvIdRef.current = id;
          setActiveConversationId(id);
        }
        await appendMessage(id, "user", text);
        return id;
      } catch {
        // best-effort: swallow — streaming proceeds regardless
        return activeConvIdRef.current;
      }
    })();
    pendingConvIdRef.current = persistPromise;

    send({ model, text, system: system.trim() || undefined, temperature, webSearch });
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

  // v43: sidebar callbacks
  function handleNew() {
    reset();
    activeConvIdRef.current = null;
    setActiveConversationId(null);
  }

  function handleSelect(id: string) {
    void (async () => {
      try {
        const conv = await getConversation(id);
        load(conv.messages.map((m) => ({ role: m.role, content: m.content })));
        activeConvIdRef.current = id;
        setActiveConversationId(id);
      } catch {
        // best-effort: if fetch fails just leave the current thread intact
      }
    })();
  }

  const showEmpty = messages.length === 0 && !isStreaming && status !== "error";

  return (
    <div className="flex h-full min-h-0 flex-row bg-muted/30">
      {/* v43: conversation history sidebar */}
      <ChatHistorySidebar
        activeId={activeConversationId}
        onSelect={handleSelect}
        onNew={handleNew}
        refreshKey={refreshKey}
        streaming={isStreaming}
      />
      <div className="flex min-h-0 flex-1 flex-col bg-muted/30">
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
            webSearch={webSearch}
            onWebSearchChange={setWebSearch}
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
    </div>
  );
}

function MessageBubble({ message, streaming = false }: { message: ChatMessage; streaming?: boolean }) {
  const isUser = message.role === "user";
  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[85%] rounded-2xl px-4 py-2.5 text-sm",
          // User text is literal (preserve newlines); assistant text is Markdown.
          isUser
            ? "whitespace-pre-wrap bg-primary text-primary-foreground"
            : "border border-border bg-background text-foreground",
        )}
      >
        {isUser ? (
          message.content
        ) : (
          <MessageMarkdown content={message.content} />
        )}
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
