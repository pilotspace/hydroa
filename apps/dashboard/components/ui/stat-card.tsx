import * as React from "react";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { cn } from "@/lib/cn";
import { Card, CardContent, CardHeader } from "./card";

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
}

const DELTA_META: Record<
  StatDelta["direction"],
  { Icon: React.ComponentType<{ className?: string }>; word: string; tone: string }
> = {
  up: { Icon: ArrowUpRight, word: "increase", tone: "text-success" },
  down: { Icon: ArrowDownRight, word: "decrease", tone: "text-destructive" },
  neutral: { Icon: Minus, word: "no change", tone: "text-muted-foreground" },
};

export function StatCard({ label, value, delta, icon, footer, className, valueTestId }: StatCardProps) {
  const meta = delta ? DELTA_META[delta.direction] : null;
  return (
    <Card data-slot="stat-card" className={cn("gap-0", className)}>
      <CardHeader className="flex-row items-center justify-between gap-2 pb-2">
        <span className="text-sm font-medium text-muted-foreground">{label}</span>
        {icon ? (
          <span className="text-muted-foreground" aria-hidden="true">
            {icon}
          </span>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        <div data-testid={valueTestId} className="text-2xl font-semibold tracking-tight text-foreground">
          {value}
        </div>
        {delta && meta ? (
          <div className={cn("inline-flex items-center gap-1 text-sm font-medium", meta.tone)}>
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
