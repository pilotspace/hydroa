"use client";

/**
 * LogDetailDrawer — the edge-anchored detail panel for one request log (M4/M5/M6/M7/M8).
 * Fetches GET /admin/logs/{id} when `logId` is non-null; hosts four independent
 * sub-panels (Overview / Request / Response / Guardrail Verdict) and the Replay control.
 *
 * Radix Dialog (via DrawerContent) supplies the focus-trap, Escape-to-close, and
 * overlay-click-close behavior unchanged. Focus-RETURN is the one piece Radix's Dialog
 * cannot infer on its own here: its built-in `onCloseAutoFocus` refocuses
 * `DialogTrigger`'s own ref specifically — not "whatever was focused before" generically —
 * and this drawer is deliberately triggerless (one shared Dialog instance opened from
 * whichever row's activation set `logId`, not a per-row `<DialogTrigger>`). So this
 * component captures `document.activeElement` (the row, already focused by LogsTable's own
 * activation handler) the moment `logId` transitions to non-null, and supplies it to
 * `onCloseAutoFocus` — preventing Radix's default (a no-op `context.triggerRef.current?.focus()`
 * against a null trigger ref) and restoring focus to the real triggering row instead. Every
 * OTHER Radix Dialog behavior (trap, Escape, overlay-click) is untouched.
 *
 * `keysById` is deliberately NOT a prop (the frozen §3 CONTRACT's LogDetailDrawerProps is
 * exactly `{ logId, onClose, onReplay }`) — the Key row shows the raw key_id verbatim, the
 * one honest option available within the frozen interface.
 */

import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { bffGet, BffError } from "@/lib/bff-client";
import {
  Badge,
  Dialog,
  DrawerContent,
  DialogTitle,
  DialogDescription,
  Loading,
  ErrorState,
} from "@/components/ui";
import { formatNumber, formatTimestamp, formatUsd } from "@/lib/format";

export interface LogDetail {
  id: string;
  created_at: string;
  model_id: string;
  key_id: string;
  status_code: number;
  stream: boolean;
  cached: boolean;
  cost_usd: string | null;
  latency_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  request_id: string | null;
  request_body: { messages: Array<{ role: string; content: unknown }> } | null;
  response_body: { content: string } | null;
  scrub_status: "scrubbed" | "scrub_failed_metadata_only" | "oversize_metadata_only";
  truncated: boolean;
  guardrail_verdict: {
    blocked: boolean;
    blocked_by: string | null;
    pii_masked: boolean;
    patterns_hit?: string[];
  } | null;
}

export interface LogDetailDrawerProps {
  /** null = closed */
  logId: string | null;
  onClose: () => void;
  /** writes sessionStorage + navigates to /app/chat */
  onReplay: (detail: LogDetail) => void;
}

const UNAVAILABLE_MESSAGE =
  "Content unavailable — this call's payload wasn't stored (scrub failed or exceeded the size limit).";

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "Failed to load log detail";
}

function OverviewPanel({ detail }: { detail: LogDetail }) {
  const ok = detail.status_code < 400;
  return (
    <section aria-labelledby="log-overview-heading" className="flex flex-col gap-2">
      <h3 id="log-overview-heading" className="text-sm font-semibold text-foreground">
        Overview
      </h3>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <dt className="text-muted-foreground">Model</dt>
        <dd className="text-foreground">{detail.model_id}</dd>

        <dt className="text-muted-foreground">Key</dt>
        <dd className="text-foreground">{detail.key_id}</dd>

        <dt className="text-muted-foreground">Status</dt>
        <dd>
          <Badge variant={ok ? "success" : "destructive"}>{detail.status_code}</Badge>
        </dd>

        <dt className="text-muted-foreground">Cost</dt>
        <dd className="text-foreground">{formatUsd(detail.cost_usd)}</dd>

        <dt className="text-muted-foreground">Timestamp</dt>
        <dd className="text-foreground">{formatTimestamp(detail.created_at)}</dd>

        <dt className="text-muted-foreground">Stream / Cached</dt>
        <dd className="text-foreground">
          {detail.stream ? "Stream" : "Non-stream"} · {detail.cached ? "Cached" : "Not cached"}
        </dd>

        <dt className="text-muted-foreground">Latency</dt>
        <dd className="text-foreground">
          {detail.latency_ms === null ? "—" : `${formatNumber(detail.latency_ms)} ms`}
        </dd>

        <dt className="text-muted-foreground">Tokens (prompt / completion / total)</dt>
        <dd className="text-foreground">
          {formatNumber(detail.prompt_tokens)} / {formatNumber(detail.completion_tokens)} /{" "}
          {formatNumber(detail.total_tokens)}
        </dd>

        <dt className="text-muted-foreground">Request ID</dt>
        <dd className="font-mono text-xs text-foreground">{detail.request_id ?? "—"}</dd>
      </dl>
    </section>
  );
}

function RequestPanel({ detail }: { detail: LogDetail }) {
  return (
    <section aria-labelledby="log-request-heading" className="flex flex-col gap-2">
      <h3 id="log-request-heading" className="text-sm font-semibold text-foreground">
        Request
      </h3>
      {detail.request_body === null ? (
        <p className="text-sm text-muted-foreground">{UNAVAILABLE_MESSAGE}</p>
      ) : (
        <details open className="rounded-md border border-border">
          <summary className="cursor-pointer px-3 py-2 text-sm text-foreground">
            Request messages
          </summary>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words border-t border-border bg-muted/30 p-3 font-mono text-xs text-foreground">
            {JSON.stringify(detail.request_body.messages, null, 2)}
          </pre>
        </details>
      )}
    </section>
  );
}

function ResponsePanel({ detail }: { detail: LogDetail }) {
  return (
    <section aria-labelledby="log-response-heading" className="flex flex-col gap-2">
      <h3 id="log-response-heading" className="text-sm font-semibold text-foreground">
        Response
      </h3>
      {detail.response_body === null ? (
        <p className="text-sm text-muted-foreground">{UNAVAILABLE_MESSAGE}</p>
      ) : (
        <details open className="rounded-md border border-border">
          <summary className="cursor-pointer px-3 py-2 text-sm text-foreground">
            Response content
          </summary>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words border-t border-border bg-muted/30 p-3 font-mono text-xs text-foreground">
            {detail.response_body.content}
          </pre>
        </details>
      )}
    </section>
  );
}

function GuardrailPanel({ detail }: { detail: LogDetail }) {
  const verdict = detail.guardrail_verdict;
  return (
    <section aria-labelledby="log-guardrail-heading" className="flex flex-col gap-2">
      <h3 id="log-guardrail-heading" className="text-sm font-semibold text-foreground">
        Guardrail Verdict
      </h3>
      {verdict === null ? (
        <p className="text-sm text-muted-foreground">No guardrail verdict recorded.</p>
      ) : (
        <div className="flex flex-col gap-1.5 text-sm">
          <div className="flex items-center gap-2">
            {verdict.blocked ? (
              <Badge variant="destructive">Blocked</Badge>
            ) : (
              <Badge variant="success">Clean</Badge>
            )}
            {verdict.blocked && verdict.blocked_by ? (
              <span className="text-foreground">{verdict.blocked_by}</span>
            ) : null}
          </div>
          {verdict.pii_masked ? (
            <p className="text-xs text-muted-foreground">PII masked</p>
          ) : null}
        </div>
      )}
    </section>
  );
}

export function LogDetailDrawer({ logId, onClose, onReplay }: LogDetailDrawerProps) {
  const detailQuery = useQuery<LogDetail>({
    queryKey: ["admin-log-detail", logId],
    queryFn: () => bffGet<LogDetail>(`/admin/logs/${logId}`),
    enabled: logId !== null,
    retry: false,
  });

  const detail = detailQuery.data;
  const notFound = detailQuery.isError && detailQuery.error instanceof BffError && detailQuery.error.status === 404;
  const replayDisabled = !detail || detail.request_body === null;

  // M4/M9: capture the triggering row (already focused by LogsTable's own activation
  // handler) at the instant the drawer opens, so it can be restored on close — see the
  // module doc comment for why Radix's own onCloseAutoFocus can't infer this on its own
  // without a <DialogTrigger>.
  const triggerElementRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (logId !== null && typeof document !== "undefined") {
      triggerElementRef.current = document.activeElement as HTMLElement | null;
    }
  }, [logId]);

  return (
    <Dialog
      open={logId !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DrawerContent
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          triggerElementRef.current?.focus();
        }}
      >
        <DialogTitle>Log detail</DialogTitle>
        <DialogDescription className="sr-only">
          Full detail for one request log, including request, response, and guardrail verdict.
        </DialogDescription>

        <div className="mt-4 flex flex-col gap-6">
          {detailQuery.isLoading ? (
            <Loading label="Loading log detail…" />
          ) : detailQuery.isError ? (
            <ErrorState title={notFound ? "Log not found" : getErrorTitle(detailQuery.error)} />
          ) : detail ? (
            <>
              <OverviewPanel detail={detail} />
              <RequestPanel detail={detail} />
              <ResponsePanel detail={detail} />
              <GuardrailPanel detail={detail} />

              <div className="flex flex-col gap-1.5 border-t border-border pt-4">
                <button
                  type="button"
                  aria-disabled={replayDisabled}
                  aria-describedby={replayDisabled ? "log-replay-reason" : undefined}
                  onClick={() => {
                    if (replayDisabled || !detail) return;
                    onReplay(detail);
                  }}
                  className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm transition-all hover:bg-primary-hover aria-disabled:pointer-events-none aria-disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                >
                  Replay in Chat
                </button>
                {replayDisabled ? (
                  <p id="log-replay-reason" className="text-xs text-muted-foreground">
                    Nothing to replay — this call&apos;s request wasn&apos;t captured.
                  </p>
                ) : null}
              </div>
            </>
          ) : null}
        </div>
      </DrawerContent>
    </Dialog>
  );
}
