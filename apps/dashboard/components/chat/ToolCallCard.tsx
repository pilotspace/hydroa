"use client";

/**
 * components/chat/ToolCallCard.tsx — render one model-requested tool call.
 *
 * chat-tools-functions. Shows the function name, pretty-printed arguments, and the
 * tool_call_id. When `onResult` is supplied (the pending turn), it also offers a
 * result textarea so the operator can answer the call before continuing the run.
 * Read-only (no input) once the turn has been answered.
 */

import { Wrench } from "lucide-react";

/** Pretty-print the streamed argument JSON; fall back to the raw text if it doesn't parse. */
function prettyArgs(args: string): string {
  const t = args.trim();
  if (!t) return "{}";
  try {
    return JSON.stringify(JSON.parse(t), null, 2);
  } catch {
    return args;
  }
}

export interface ToolCallView {
  id: string;
  name: string;
  arguments: string;
}

export function ToolCallCard({
  call,
  result,
  onResult,
}: {
  call: ToolCallView;
  result?: string;
  onResult?: (value: string) => void;
}) {
  return (
    <div className="rounded-xl border border-border bg-background p-3 text-sm">
      <div className="flex items-center gap-2">
        <Wrench className="size-3.5 text-muted-foreground" aria-hidden="true" />
        <span className="font-mono font-medium text-foreground">{call.name}</span>
        <span className="ml-auto truncate font-mono text-xs text-muted-foreground">{call.id}</span>
      </div>
      <pre className="mt-2 overflow-x-auto rounded-md bg-muted/40 p-2 text-xs text-foreground">
        {prettyArgs(call.arguments)}
      </pre>
      {onResult ? (
        <div className="mt-2 flex flex-col gap-1">
          <span className="text-xs font-medium text-foreground">Result</span>
          <textarea
            aria-label="Tool result"
            value={result ?? ""}
            onChange={(e) => onResult(e.target.value)}
            rows={2}
            placeholder="Paste the tool's result (JSON or text)…"
            className="w-full resize-y rounded-md border border-border bg-background px-2 py-1 font-mono text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </div>
      ) : null}
    </div>
  );
}
