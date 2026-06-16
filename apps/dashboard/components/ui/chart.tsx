"use client";

import * as React from "react";
import { Tooltip } from "recharts";
import { cn } from "@/lib/cn";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./card";

/**
 * Chart primitives — a thin, token-driven wrapper over Recharts.
 *
 * ChartContainer publishes each series color as a `--color-<key>` CSS custom property from
 * the config, so consumers reference `var(--color-<key>)` (which itself resolves to a
 * `--color-chart-N` token) — NO raw hex ever lives in a component file (R3). ChartCard wraps
 * a titled Card around any chart the caller composes inside a ChartContainer.
 */

export type ChartConfig = Record<string, { label: string; color?: string }>;

export interface ChartContainerProps {
  config: ChartConfig;
  className?: string;
  children: React.ReactNode;
}

export function ChartContainer({ config, className, children }: ChartContainerProps) {
  const style: Record<string, string> = {};
  for (const [key, item] of Object.entries(config)) {
    if (item.color) style[`--color-${key}`] = item.color;
  }
  return (
    <div data-slot="chart" style={style as React.CSSProperties} className={cn("aspect-video w-full", className)}>
      {children}
    </div>
  );
}

/** Recharts Tooltip, re-exported under the design-system name. */
export const ChartTooltip = Tooltip;

interface ChartTooltipContentProps {
  active?: boolean;
  label?: React.ReactNode;
  payload?: { name?: string; value?: React.ReactNode; color?: string }[];
}

export function ChartTooltipContent({ active, label, payload }: ChartTooltipContentProps) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-sm text-popover-foreground shadow-md">
      {label != null ? <div className="mb-1 font-medium">{label}</div> : null}
      <ul className="flex flex-col gap-1">
        {payload.map((entry, i) => (
          <li key={i} className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="inline-block size-2 rounded-full"
              style={{ backgroundColor: entry.color }}
            />
            <span className="text-muted-foreground">{entry.name}</span>
            <span className="ml-auto font-medium">{entry.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export interface ChartCardProps {
  title: string;
  description?: string;
  config: ChartConfig;
  className?: string;
  children: React.ReactNode;
}

export function ChartCard({ title, description, className, children }: ChartCardProps) {
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description ? <CardDescription>{description}</CardDescription> : null}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}
