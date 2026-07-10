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

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { Bot, Check, Copy, Paperclip, RefreshCw, Send, Square, User } from "lucide-react";
import {
  useChatStream,
  type ChatMessage,
  type MessageToolCall,
  type TurnMeta,
  type Usage,
} from "@/lib/hooks/use-chat-stream";
import { parseToolDef, type ToolChoice, type ToolDef } from "@/lib/chat/tool-defs";
import {
  fileToAttachment,
  attachmentErrorMessage,
  contentText,
  contentImages,
  ATTACHMENT_LIMIT_MESSAGE,
  MAX_IMAGES_PER_MESSAGE,
  ALLOWED_MIME,
  type ImageAttachment,
} from "@/lib/chat/attachments";
import { StagedAttachments, MessageImages } from "@/components/chat/AttachmentPreview";
import { bffGet } from "@/lib/bff-client";
import { consumeReplayPayload, type ReplayPayload } from "@/lib/logs-replay";
import type { ModelsData } from "@/components/models/ModelCatalogTable";
import { ChatHistorySidebar } from "@/components/chat/ChatHistorySidebar";
import { ModelPicker } from "@/components/chat/ModelPicker";
import { MessageMarkdown } from "@/components/chat/MessageMarkdown";
import { ToolCallCard } from "@/components/chat/ToolCallCard";
import { InspectorPanel, EMPTY_SAMPLING, type SamplingState } from "@/components/chat/InspectorPanel";
import { ConversationTopBar } from "@/components/chat/ConversationTopBar";
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

/**
 * Spread the live sampling state into a send() input. Each field is passed as-is;
 * useChatStream omits any key that is unset/invalid OR not honored by the model's
 * provider, so the off/default path stays byte-identical and a dropped param never
 * ships. (chat-parameters-panel)
 */
function samplingToInput(s: SamplingState) {
  return {
    topP: s.topP,
    maxTokens: s.maxTokens,
    frequencyPenalty: s.frequencyPenalty,
    presencePenalty: s.presencePenalty,
    seed: s.seed,
    stop: s.stop,
    responseFormat: s.responseFormat,
  };
}

/** Composer quick-action chips (design parity) — each prefills the input. */
const QUICK_ACTIONS: ReadonlyArray<{ label: string; prompt: string }> = [
  { label: "Explain code", prompt: "Explain this code:\n\n" },
  { label: "Summarize", prompt: "Summarize the following:\n\n" },
  { label: "Improve writing", prompt: "Improve the writing of:\n\n" },
];

/**
 * Initials from an email local-part — real identity for the user avatar, no
 * fabrication. "ada.lovelace@x" → "AL"; "ada@x" → "AD". Null when unknown.
 */
function initialsFromEmail(email: string | null | undefined): string | null {
  if (!email) return null;
  const local = email.split("@")[0] ?? "";
  const parts = local.split(/[._-]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  const s = parts[0] ?? local;
  return (s.slice(0, 2) || s.slice(0, 1)).toUpperCase() || null;
}

/** Coarse token estimate (~4 chars/token) — labeled as an estimate, never billed. */
function estimateTokens(text: string): number {
  const t = text.trim();
  return t ? Math.max(1, Math.ceil(t.length / 4)) : 0;
}

/** Wall-clock HH:MM for a turn's commit time (absent → null, shown as nothing). */
function formatClock(at: number | undefined): string | null {
  if (!at) return null;
  try {
    return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return null;
  }
}

/**
 * The per-turn meta line for an assistant reply:
 *   "model · stop · 11p / 4c 15t · 0.9s · $0.0041"
 * Order: model · finish_reason · tokens (Xp / Yc Zt) · latency · cost.
 * Every part is REAL (from TurnMeta + catalog pricing) or omitted — no fabrication.
 */
function formatTurnMeta(meta: TurnMeta | undefined, costText: string | null): string {
  if (!meta) return "";
  const parts: string[] = [];
  if (meta.model) parts.push(meta.model);
  if (meta.finishReason) parts.push(meta.finishReason);
  if (meta.usage) {
    const { prompt_tokens: p, completion_tokens: c, total_tokens: t } = meta.usage;
    parts.push(`${p}p/${c}c ${t}tok`);
  }
  if (meta.latencyMs != null) parts.push(`${(meta.latencyMs / 1000).toFixed(1)}s`);
  if (costText) parts.push(costText);
  return parts.join(" · ");
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

  const {
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
  } = useChatStream({ onTurnComplete });

  const [input, setInput] = useState("");
  const [model, setModel] = useState(defaultModel);
  const [system, setSystem] = useState("");
  const [temperature, setTemperature] = useState(1);
  const [webSearch, setWebSearch] = useState(false);
  // chat-parameters-panel: live sampling controls — in-memory, persists across
  // turns + inspector tab switches. A shallow patch keeps unrelated fields intact.
  const [sampling, setSampling] = useState<SamplingState>(EMPTY_SAMPLING);
  const onSampling = useCallback(
    (patch: Partial<SamplingState>) => setSampling((s) => ({ ...s, ...patch })),
    [],
  );
  // chat-tools-functions: tools authored as raw JSON drafts (one per editor) + the tool_choice.
  // The valid subset (parses + shape-checks) is what reaches the wire; an invalid draft is
  // flagged in the editor and excluded. Zero valid tools ⇒ no tools/tool_choice (byte-identical).
  const [toolDrafts, setToolDrafts] = useState<string[]>([]);
  const [toolChoice, setToolChoice] = useState<ToolChoice>("auto");
  const validTools = useMemo<ToolDef[]>(
    () => toolDrafts.map((d) => parseToolDef(d)).filter((d): d is ToolDef => d !== null),
    [toolDrafts],
  );
  const toolsInput = () => ({
    tools: validTools.length > 0 ? validTools : undefined,
    toolChoice,
  });
  // Per-tool_call_id result text the operator types before continuing (cleared on continue).
  const [toolResults, setToolResults] = useState<Record<string, string>>({});
  // chat-attachments: staged images for the next turn + the last rejection message.
  // `imageNames` keeps url→filename so a committed turn (whose wire content-part is
  // only {image_url:{url}}) can still alt-text its thumbnails.
  const [attachments, setAttachments] = useState<ImageAttachment[]>([]);
  const [attachError, setAttachError] = useState<string | null>(null);
  const [imageNames, setImageNames] = useState<Map<string, string>>(new Map());
  const attachmentsRef = useRef<ImageAttachment[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    attachmentsRef.current = attachments;
  }, [attachments]);
  // Validate + encode each pick; enforce the per-message count cap against the
  // running staged set (design-for-failure — reject before building a data-URL).
  const onPickFiles = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setAttachError(null);
    for (const file of Array.from(files)) {
      // Enforce the cap against the LIVE staged count (the ref is bumped
      // synchronously on each admit) so concurrent onPickFiles invocations
      // can't each read a stale snapshot and overflow it.
      if (attachmentsRef.current.length >= MAX_IMAGES_PER_MESSAGE) {
        setAttachError(ATTACHMENT_LIMIT_MESSAGE);
        break;
      }
      const res = await fileToAttachment(file);
      if (!res.ok) {
        setAttachError(attachmentErrorMessage(res.error));
        continue;
      }
      // re-check after the async encode — another pick may have filled the cap
      if (attachmentsRef.current.length >= MAX_IMAGES_PER_MESSAGE) {
        setAttachError(ATTACHMENT_LIMIT_MESSAGE);
        break;
      }
      const att = res.attachment;
      const nextStaged = [...attachmentsRef.current, att];
      attachmentsRef.current = nextStaged; // bump synchronously; the effect reconciles
      setAttachments(nextStaged);
      setImageNames((cur) => new Map(cur).set(att.dataUrl, att.name));
    }
  }, []);
  const removeAttachment = useCallback((id: string) => {
    setAttachments((cur) => cur.filter((a) => a.id !== id));
  }, []);
  const [sessionTokens, setSessionTokens] = useState(0);
  // The conversation title shown in the top bar (null → a fresh, unsaved chat).
  const [activeTitle, setActiveTitle] = useState<string | null>(null);
  const countedRef = useRef<Usage | undefined>(undefined);
  const threadEndRef = useRef<HTMLDivElement>(null);

  // Real user initials for the avatar — a lightweight /api/auth/me read (same
  // cookie-auth pattern as the sidebar; no react-query so the bare component
  // works standalone). Honest degrade: a generic glyph until/if it resolves.
  const [userInitials, setUserInitials] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    fetch("/api/auth/me", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((u: { email?: string | null } | null) => {
        if (alive && u) setUserInitials(initialsFromEmail(u.email));
      })
      .catch(() => {
        /* honest degrade — keep the generic avatar */
      });
    return () => {
      alive = false;
    };
  }, []);

  // Per-model catalog pricing for honest per-turn cost (per-token leaves on the
  // JWT/control-plane twin). Plain bffGet (no react-query) so the component stays
  // standalone; honest degrade to no-cost when a model has no price.
  //
  // logs-explorer-ui (v58): this same fetch also captures the live catalog's id set
  // (`catalogModelIds`), reused below to validate a replayed model_id (M6) — sharing the
  // one existing request rather than adding a second, redundant /admin/catalog/models call.
  const [priceMap, setPriceMap] = useState<Map<string, { p: number; c: number }>>(new Map());
  const [catalogModelIds, setCatalogModelIds] = useState<Set<string> | null>(null);
  useEffect(() => {
    let alive = true;
    bffGet<ModelsData>("/admin/catalog/models")
      .then((d) => {
        if (!alive) return;
        const m = new Map<string, { p: number; c: number }>();
        for (const e of d.data ?? []) {
          if (typeof e.prompt_per_token === "number" && typeof e.completion_per_token === "number") {
            m.set(e.id, { p: e.prompt_per_token, c: e.completion_per_token });
          }
        }
        setPriceMap(m);
        setCatalogModelIds(new Set((d.data ?? []).map((e) => e.id)));
      })
      .catch(() => {
        // honest degrade — turns show tokens but no dollar cost; an empty (non-null) set
        // lets a pending replay fall back rather than waiting forever (below).
        if (alive) setCatalogModelIds(new Set());
      });
    return () => {
      alive = false;
    };
  }, []);

  // logs-explorer-ui (v58): consume a one-time Logs Explorer replay handoff. Read exactly
  // once on mount (the ref guard survives StrictMode's double-invoke) — consumeReplayPayload()
  // clears the sessionStorage entry immediately, so back-navigation never re-triggers it.
  const [replayPayload, setReplayPayload] = useState<ReplayPayload | null>(null);
  const replayConsumedRef = useRef(false);
  const [replayNotice, setReplayNotice] = useState<{ fallbackModel: string | null; degraded: boolean }>({
    fallbackModel: null,
    degraded: false,
  });
  useEffect(() => {
    if (replayConsumedRef.current) return;
    replayConsumedRef.current = true;
    // One-shot sync from an external store (sessionStorage) — the SSR-safe pattern for
    // a browser-only read; not a cascading render. (rule is over-strict here; same
    // escape hatch as LoginForm's SSO-domain seed.) consumeReplayPayload() also clears
    // the entry, so this must stay an effect (a mutating external read has no place in
    // a render body) rather than the render-time "adjust state" pattern used below.
    const payload = consumeReplayPayload();
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (payload) setReplayPayload(payload);
  }, []);

  // Apply the consumed payload once the live catalog resolves (never hangs: the catalog
  // fetch above always settles catalogModelIds, success or fail-open-empty). M6: the
  // composer is pre-filled but NEVER auto-sent — only `input`/`model` state is seeded.
  // React's documented "adjust state during render" escape hatch (same idiom as
  // SpendPage's lastGood tracking) — NOT a useEffect, so react-hooks/set-state-in-effect
  // stays clean: this is pure derived-state from `replayPayload`/`catalogModelIds`, and
  // clearing `replayPayload` at the end makes the block self-terminating (no re-entry).
  if (replayPayload && catalogModelIds !== null) {
    // Only pre-fill an UNTOUCHED composer: the catalog resolves async, so a user who
    // started typing before it settled must not have their in-progress text clobbered.
    // Either way the payload is one-shot — consumed and cleared, never re-applied.
    if (input === "") {
      const inCatalog = catalogModelIds.has(replayPayload.modelId);
      setInput(replayPayload.text);
      setModel(inCatalog ? replayPayload.modelId : defaultModel);
      setReplayNotice({
        fallbackModel: inCatalog ? null : replayPayload.modelId,
        degraded: replayPayload.degraded,
      });
    }
    setReplayPayload(null);
  }

  const costFor = useCallback(
    (tm: TurnMeta | undefined): string | null => {
      if (!tm?.usage || !tm.model) return null;
      const price = priceMap.get(tm.model);
      if (!price) return null;
      const cost = tm.usage.prompt_tokens * price.p + tm.usage.completion_tokens * price.c;
      return Number.isFinite(cost) ? `$${cost.toFixed(4)}` : null;
    },
    [priceMap],
  );

  const isStreaming = status === "streaming";

  // Regenerate the assistant reply for a turn: truncate the thread to just
  // before that user message and re-send it with the live model settings. Uses
  // the hook's synchronous messagesRef seam (load → send composes correctly).
  const regenerateFrom = useCallback(
    (assistantIndex: number) => {
      if (isStreaming) return;
      const userMsg = messages[assistantIndex - 1];
      if (!userMsg || userMsg.role !== "user") return;
      load(messages.slice(0, assistantIndex - 1));
      // chat-attachments: regenerate re-sends the same turn — reconstruct the image
      // parts (text + dataURLs) so a multimodal turn keeps its images.
      const urls = contentImages(userMsg.content);
      const images: ImageAttachment[] | undefined =
        urls.length > 0
          ? urls.map((url, i) => ({
              id: `regen-${assistantIndex}-${i}`,
              name: imageNames.get(url) ?? "image",
              mime: url.startsWith("data:") ? url.slice(5, url.indexOf(";")) : "image/png",
              dataUrl: url,
              bytes: 0,
            }))
          : undefined;
      send({
        model,
        text: contentText(userMsg.content),
        system: system || undefined,
        temperature,
        webSearch,
        ...samplingToInput(sampling),
        tools: validTools.length > 0 ? validTools : undefined,
        toolChoice,
        images,
      });
    },
    [isStreaming, messages, load, send, model, system, temperature, webSearch, sampling, validTools, toolChoice, imageNames],
  );

  // Accumulate the session token total — each completed turn's usage object is
  // counted EXACTLY once (the identity guard survives StrictMode double-invoke
  // and any re-render where `usage` is unchanged).
  useEffect(() => {
    if (usage && usage !== countedRef.current) {
      countedRef.current = usage;
      setSessionTokens((t) => t + usage.total_tokens);
    }
  }, [usage]);

  // Session cost: derived from the committed meta array + catalog pricing.
  // Each TurnMeta already carries the model that produced the turn, so no
  // extra ref is needed. Returns null (honest-absent) until a priced turn exists.
  const sessionCost = useMemo<number | null>(() => {
    let total = 0;
    let hasAny = false;
    for (const m of meta) {
      if (!m.usage || !m.model) continue;
      const price = priceMap.get(m.model);
      if (!price) continue;
      const c = m.usage.prompt_tokens * price.p + m.usage.completion_tokens * price.c;
      if (Number.isFinite(c) && c > 0) {
        total += c;
        hasAny = true;
      }
    }
    return hasAny ? total : null;
  }, [meta, priceMap]);

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

    send({
      model,
      text,
      system: system.trim() || undefined,
      temperature,
      webSearch,
      ...samplingToInput(sampling),
      ...toolsInput(),
      // chat-attachments: ≥1 staged image ⇒ the user turn ships as content-parts.
      images: attachments.length > 0 ? attachments : undefined,
    });
    setInput("");
    // clear the staged set + any rejection message for the next turn
    setAttachments([]);
    setAttachError(null);
  }

  // chat-tools-functions: answer the pending tool calls (partial-friendly — unanswered ⇒ "")
  // and continue the run. The hook appends one role:"tool" message per pending call and re-streams
  // with the same tools/model/sampling.
  function handleContinue() {
    submitToolResults(pendingToolCalls.map((c) => ({ tool_call_id: c.id, content: toolResults[c.id] ?? "" })));
    setToolResults({});
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
    setActiveTitle(null);
  }

  function handleSelect(id: string) {
    void (async () => {
      try {
        const conv = await getConversation(id);
        load(conv.messages.map((m) => ({ role: m.role, content: m.content })));
        activeConvIdRef.current = id;
        setActiveConversationId(id);
        setActiveTitle(conv.title ?? "Untitled");
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
      {/* CONVERSATION column — top bar + streaming thread + composer. */}
      <div className="flex min-h-0 flex-1 flex-col bg-muted/30">
      <ConversationTopBar
        title={activeTitle ?? "New chat"}
        modelPicker={<ModelPicker value={model} onChange={setModel} />}
        costReadout={<CostReadout sessionTokens={sessionTokens} lastTurn={usage} sessionCost={sessionCost} />}
      />

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

          {messages.length > 0 ? <DayDivider label="Today" /> : null}

          {messages.map((m, i) => {
            // chat-tools-functions: a role:"tool" message is the operator's answer — captured in
            // the card's input, not shown as its own bubble.
            if (m.role === "tool") return null;
            // an assistant turn that asked to call tools renders as ToolCallCards; the LAST such
            // turn while awaiting_tool is interactive (result inputs + Continue).
            if (m.role === "assistant" && m.tool_calls && m.tool_calls.length > 0) {
              const pending = status === "awaiting_tool" && i === messages.length - 1;
              return (
                <ToolCallTurn
                  key={i}
                  calls={m.tool_calls}
                  pending={pending}
                  results={toolResults}
                  onResult={(id, v) => setToolResults((r) => ({ ...r, [id]: v }))}
                  onContinue={handleContinue}
                />
              );
            }
            return (
              <MessageRow
                key={i}
                message={m}
                meta={meta[i]}
                costText={costFor(meta[i])}
                userInitials={userInitials}
                imageNames={imageNames}
                onRegenerate={
                  m.role === "assistant" && !isStreaming ? () => regenerateFrom(i) : undefined
                }
              />
            );
          })}

          {isStreaming ? (
            <MessageRow
              message={{ role: "assistant", content: streamingText }}
              userInitials={userInitials}
              streaming
            />
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
          {/* logs-explorer-ui (v58): one-time replay notices — the composer is prefilled but
              never auto-sent (M6/M8); each notice is purely informational (role="status"). */}
          {replayNotice.fallbackModel ? (
            <p role="status" className="px-1 text-xs text-muted-foreground">
              Model no longer available — defaulted to {defaultModel}.
            </p>
          ) : null}
          {replayNotice.degraded ? (
            <p role="status" className="px-1 text-xs text-muted-foreground">
              Some content couldn&apos;t be replayed.
            </p>
          ) : null}
          {/* Quick-action chips — design parity; prefill the composer when idle+empty. */}
          {input.trim() === "" && !isStreaming ? (
            <div className="flex flex-wrap gap-1.5">
              {QUICK_ACTIONS.map((qa) => (
                <button
                  key={qa.label}
                  type="button"
                  onClick={() => setInput(qa.prompt)}
                  className="rounded-full border border-border bg-muted/50 px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {qa.label}
                </button>
              ))}
            </div>
          ) : null}
          {/* chat-attachments: staged image strip + any rejection message. */}
          {attachments.length > 0 ? (
            <StagedAttachments items={attachments} onRemove={removeAttachment} />
          ) : null}
          {attachError ? (
            <p role="alert" className="px-1 text-xs text-destructive">
              {attachError}
            </p>
          ) : null}
          <div className="flex items-end gap-2 rounded-xl border border-border bg-background p-2 focus-within:ring-2 focus-within:ring-ring">
          {/* chat-attachments: pick image(s) → stage as content-parts. */}
          <input
            ref={fileInputRef}
            type="file"
            accept={ALLOWED_MIME.join(",")}
            multiple
            data-testid="attachment-input"
            className="hidden"
            onChange={(e) => {
              void onPickFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => fileInputRef.current?.click()}
            aria-label="Attach image"
            className="size-9 shrink-0 text-muted-foreground"
          >
            <Paperclip className="size-4" aria-hidden="true" />
          </Button>
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
          {/* Footer hint + live token estimate (design parity). */}
          <div className="flex items-center justify-between px-1 text-xs text-muted-foreground">
            <span>Enter to send · Shift+Enter for newline</span>
            {input.trim() ? <span>~{estimateTokens(input)} tokens</span> : <span aria-hidden="true" />}
          </div>
        </div>
      </form>
      </div>

      {/* INSPECTOR panel — Parameters / Tools / Code (collapses below xl). */}
      <InspectorPanel
        model={model}
        sampling={sampling}
        onSampling={onSampling}
        system={system}
        onSystemChange={setSystem}
        temperature={temperature}
        onTemperatureChange={setTemperature}
        webSearch={webSearch}
        onWebSearchChange={setWebSearch}
        toolDrafts={toolDrafts}
        onToolDrafts={setToolDrafts}
        toolChoice={toolChoice}
        onToolChoice={setToolChoice}
      />
    </div>
  );
}

/**
 * chat-tools-functions: an assistant turn that requested tool calls. Renders each call as a
 * ToolCallCard; while it is the pending turn (awaiting_tool) the cards expose result inputs and a
 * single Continue answers ALL pending calls (partial-friendly) and resumes the run.
 */
function ToolCallTurn({
  calls,
  pending,
  results,
  onResult,
  onContinue,
}: {
  calls: MessageToolCall[];
  pending: boolean;
  results: Record<string, string>;
  onResult: (id: string, value: string) => void;
  onContinue: () => void;
}) {
  return (
    <div data-role="assistant" className="flex gap-3">
      <Avatar isUser={false} initials={null} />
      <div className="flex min-w-0 max-w-[85%] flex-col gap-2">
        <span className="px-1 text-xs font-medium text-muted-foreground">Assistant · tool call</span>
        {calls.map((c) => (
          <ToolCallCard
            key={c.id}
            call={{ id: c.id, name: c.function.name, arguments: c.function.arguments }}
            result={pending ? (results[c.id] ?? "") : undefined}
            onResult={pending ? (v) => onResult(c.id, v) : undefined}
          />
        ))}
        {pending ? (
          <div className="flex items-center gap-2 px-1">
            <Button type="button" size="sm" onClick={onContinue} aria-label="Continue">
              <Send className="size-4" aria-hidden="true" />
              Continue
            </Button>
            <span className="text-xs text-muted-foreground">
              Supply results (any you can), then continue the run.
            </span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** A day separator that groups the thread (design parity). */
function DayDivider({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 py-1 text-xs text-muted-foreground">
      <span className="h-px flex-1 bg-border" />
      <span className="font-medium">{label}</span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}

/** Round avatar: real user initials, or a role glyph (honest fallback). */
function Avatar({ isUser, initials }: { isUser: boolean; initials: string | null }) {
  return (
    <div
      className={cn(
        "flex size-8 shrink-0 select-none items-center justify-center rounded-full text-xs font-semibold",
        isUser
          ? "bg-primary text-primary-foreground"
          : "border border-border bg-muted text-muted-foreground",
      )}
    >
      {isUser ? (
        initials ?? <User className="size-4" aria-hidden="true" />
      ) : (
        <Bot className="size-4" aria-hidden="true" />
      )}
    </div>
  );
}

/** Copy-to-clipboard button with a transient "Copied" state. */
function CopyTurnButton({ getText }: { getText: () => string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label="Copy"
      onClick={() => {
        const text = getText();
        if (!text || !navigator.clipboard) return;
        void navigator.clipboard.writeText(text).then(
          () => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          },
          () => {},
        );
      }}
      className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {copied ? <Check className="size-3" aria-hidden="true" /> : <Copy className="size-3" aria-hidden="true" />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

/**
 * One conversation turn: avatar + role label (+ wall-clock time) + bubble, with a
 * per-turn meta line (model · latency · tokens · cost) and Copy / Regenerate on
 * completed assistant replies (design parity). User text is literal; assistant
 * text is Markdown. Every meta value is REAL (TurnMeta + catalog pricing) or
 * omitted — never fabricated.
 */
function MessageRow({
  message,
  userInitials,
  meta,
  costText = null,
  streaming = false,
  onRegenerate,
  imageNames,
}: {
  message: ChatMessage;
  userInitials: string | null;
  meta?: TurnMeta;
  costText?: string | null;
  streaming?: boolean;
  onRegenerate?: () => void;
  /** chat-attachments: url→filename so a committed multimodal turn can alt-text its thumbnails. */
  imageNames?: Map<string, string>;
}) {
  const isUser = message.role === "user";
  const clock = formatClock(meta?.at);
  const metaLine = isUser ? "" : formatTurnMeta(meta, costText);
  // chat-attachments: a user turn may carry image parts; everything else is text.
  const text = contentText(message.content);
  const images = contentImages(message.content).map((url) => ({
    url,
    alt: imageNames?.get(url) ?? "attachment",
  }));
  return (
    <div
      data-role={message.role}
      className={cn("flex gap-3", isUser ? "flex-row-reverse" : "flex-row")}
    >
      <Avatar isUser={isUser} initials={userInitials} />
      <div
        className={cn("flex min-w-0 max-w-[85%] flex-col gap-1", isUser ? "items-end" : "items-start")}
      >
        <span className="px-1 text-xs font-medium text-muted-foreground">
          {isUser ? "You" : "Assistant"}
          {clock ? <span className="font-normal"> · {clock}</span> : null}
        </span>
        <div
          className={cn(
            "rounded-2xl px-4 py-2.5 text-sm",
            isUser
              ? "whitespace-pre-wrap bg-primary text-primary-foreground"
              : "border border-border bg-background text-foreground",
          )}
        >
          {images.length > 0 ? <MessageImages images={images} className="mb-2" /> : null}
          {isUser ? text : <MessageMarkdown content={text} />}
          {streaming ? (
            <span
              className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-current align-middle"
              aria-hidden="true"
            />
          ) : null}
        </div>
        {!isUser && !streaming ? (
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-1">
            {metaLine ? <span className="text-xs text-muted-foreground">{metaLine}</span> : null}
            <CopyTurnButton getText={() => text} />
            {onRegenerate ? (
              <button
                type="button"
                aria-label="Regenerate"
                onClick={onRegenerate}
                className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <RefreshCw className="size-3" aria-hidden="true" />
                Regenerate
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
