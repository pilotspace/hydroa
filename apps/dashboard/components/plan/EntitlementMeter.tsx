"use client";

/**
 * EntitlementMeter — generalizes MemoryScoreBar's `role="progressbar"` null-value idiom for
 * a $-ceiling instead of a 0–1 score (billing-ui TASK.md §3 CONTRACT M7).
 *
 * `ceilingUsd === null` -> the spent amount + the word "Unlimited" ONLY — no progressbar
 * role, never a fabricated percentage (mirrors MemoryScoreBar's own null-score branch).
 * `ceilingUsd !== null` -> a real progressbar, percentage capped at 100, tone flips to
 * destructive at/over the ceiling — conveyed by BOTH the fill width and the numeric label,
 * never color alone (WCAG 1.4.1, mirrors StatCard's own delta-tone discipline).
 */

import { cn } from "@/lib/cn";
import { formatUsd } from "@/lib/format";

export interface EntitlementMeterProps {
  label: string;
  valueUsd: string;
  ceilingUsd: string | null;
}

export function EntitlementMeter({ label, valueUsd, ceilingUsd }: EntitlementMeterProps) {
  const eyebrow = (
    <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</span>
  );

  if (ceilingUsd === null) {
    return (
      <div className="flex flex-col gap-1">
        {eyebrow}
        <span className="font-mono tabular-nums text-sm text-foreground">
          {formatUsd(valueUsd)} · Unlimited
        </span>
      </div>
    );
  }

  const value = Number(valueUsd) || 0;
  const ceiling = Number(ceilingUsd) || 0;
  const pct = ceiling > 0 ? Math.min(100, Math.round((value / ceiling) * 100)) : 0;
  const over = value >= ceiling;

  return (
    <div className="flex flex-col gap-1">
      {eyebrow}
      <div className="flex items-center gap-2">
        <div
          role="progressbar"
          aria-valuenow={value}
          aria-valuemin={0}
          aria-valuemax={ceiling}
          aria-label={`${label}: ${formatUsd(valueUsd)} of ${formatUsd(ceilingUsd)}`}
          className="relative h-2 flex-1 rounded-full bg-muted overflow-hidden"
        >
          <div
            className={cn("absolute inset-y-0 left-0 rounded-full transition-all", over ? "bg-destructive" : "bg-primary")}
            style={{ width: `${pct}%` }}
            aria-hidden="true"
          />
        </div>
        <span className="font-mono tabular-nums text-sm text-foreground shrink-0">
          {formatUsd(valueUsd)} / {formatUsd(ceilingUsd)}
        </span>
      </div>
    </div>
  );
}
