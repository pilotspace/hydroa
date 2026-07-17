import * as React from "react";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { cn } from "@/lib/cn";
import { Card, CardContent, CardHeader, type CardProps } from "./card";

/**
 * StatCard — a labelled KPI tile (label · value · optional trend delta · optional footer).
 * Realizes the v13 catalog `StatCard` entry. The trend DIRECTION is conveyed by an arrow
 * icon AND an sr-only word (increase/decrease/no change), never by color alone (WCAG 1.4.1);
 * color is only a reinforcement.
 */

export interface StatDelta {
  direction: "up" | "down" | "neutral";
  text: string;
}

export interface StatCardProps {
  label: string;
  value: React.ReactNode;
  delta?: StatDelta;
  icon?: React.ReactNode;
  footer?: React.ReactNode;
  className?: string;
  /** Optional data-testid placed on the value node — lets a consumer keep a frozen test hook. */
  valueTestId?: string;
  /**
   * Additive passthrough to the internal Card's own `variant` (platform-console-
   * flat-redesign, console-flat-visual-pass) — omitted stays byte-identical to
   * every existing dashboard-wide caller, since Card itself defaults to
   * "default" when its own `variant` prop is undefined. See card.tsx's own
   * header comment for the additive-only guarantee this mirrors.
   */
  variant?: CardProps["variant"];
}

const DELTA_META: Record<
  StatDelta["direction"],
  { Icon: React.ComponentType<{ className?: string }>; word: string; tone: string }
> = {
  up: { Icon: ArrowUpRight, word: "increase", tone: "bg-success/10 text-success-text" },
  down: { Icon: ArrowDownRight, word: "decrease", tone: "bg-destructive/10 text-destructive-text" },
  neutral: { Icon: Minus, word: "no change", tone: "bg-muted text-muted-foreground" },
};

export function StatCard({
  label,
  value,
  delta,
  icon,
  footer,
  className,
  valueTestId,
  variant,
}: StatCardProps) {
  const meta = delta ? DELTA_META[delta.direction] : null;
  return (
    <Card data-slot="stat-card" variant={variant} className={cn("gap-0", className)}>
      <CardHeader className="flex-row items-center justify-between gap-2 pb-2">
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</span>
        {icon ? (
          <span className="text-muted-foreground" aria-hidden="true">
            {icon}
          </span>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <div
          data-testid={valueTestId}
          className="text-3xl font-mono font-semibold tabular-nums tracking-tight text-foreground"
        >
          {value}
        </div>
        {delta && meta ? (
          <div
            className={cn(
              // artifact `.delta`: mono figures, 12px, 6px radius (rounded-md) — "mono for all data".
              // Keeps the arrow + sr-only word so direction never rides on color alone (WCAG 1.4.1).
              "inline-flex w-fit items-center gap-1 rounded-md px-2 py-0.5 text-xs font-mono font-semibold tabular-nums",
              meta.tone,
            )}
          >
            <meta.Icon className="size-4" aria-hidden="true" />
            <span>{delta.text}</span>
            <span className="sr-only">{meta.word}</span>
          </div>
        ) : null}
        {footer ? <div className="text-sm text-muted-foreground">{footer}</div> : null}
      </CardContent>
    </Card>
  );
}
