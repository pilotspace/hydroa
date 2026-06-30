"use client";

/**
 * components/memory/MemoryScoreBar.tsx — relevance score visualization.
 *
 * Rules:
 *  - score != null  → render a progressbar (role="progressbar") with aria-valuenow
 *    and display the numeric score (e.g. "0.97").
 *  - score === null → render a "text match" affordance text ONLY — NEVER fabricate
 *    a number. No progressbar is rendered in this case.
 */

interface MemoryScoreBarProps {
  score: number | null;
}

export function MemoryScoreBar({ score }: MemoryScoreBarProps) {
  if (score === null) {
    return (
      <span className="text-xs text-muted-foreground italic">text match</span>
    );
  }

  const pct = Math.round(score * 100);

  return (
    <div className="flex items-center gap-2 min-w-[5rem]">
      <div
        role="progressbar"
        aria-valuenow={score}
        aria-valuemin={0}
        aria-valuemax={1}
        aria-label={`Relevance score ${score.toFixed(2)}`}
        className="relative h-1.5 flex-1 rounded-full bg-muted overflow-hidden"
      >
        <div
          className="absolute inset-y-0 left-0 rounded-full bg-primary transition-all"
          style={{ width: `${pct}%` }}
          aria-hidden="true"
        />
      </div>
      <span className="text-xs tabular-nums text-muted-foreground w-7 text-right shrink-0">
        {score.toFixed(2)}
      </span>
    </div>
  );
}
