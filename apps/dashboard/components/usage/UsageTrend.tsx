"use client";

import { useMemo } from "react";
import { LineChart, Line, CartesianGrid, XAxis, YAxis } from "recharts";
import type { UsageRecord } from "./UsageStatsCards";
import { ChartContainer } from "@/components/ui/chart";

const CHART_CONFIG = {
  cost: { label: "Daily cost (USD)", color: "hsl(var(--color-chart-1))" },
};

interface UsageTrendProps {
  records: UsageRecord[];
}

export function UsageTrend({ records }: UsageTrendProps) {
  const data = useMemo(() => {
    const byDay: Record<string, number> = {};
    for (const rec of records) {
      const day = rec.created_at.slice(0, 10);
      byDay[day] = (byDay[day] ?? 0) + Number(rec.cost_usd);
    }
    return Object.entries(byDay)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([day, cost]) => ({ day, cost }));
  }, [records]);

  if (records.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No data to show for the selected period.
      </p>
    );
  }

  return (
    <figure data-testid="usage-trend-chart" aria-label="Usage cost trend">
      <figcaption className="sr-only">Usage cost trend over time</figcaption>
      <ChartContainer config={CHART_CONFIG} className="aspect-auto">
        <LineChart
          width={560}
          height={220}
          data={data}
          aria-hidden="true"
          role="presentation"
          accessibilityLayer={false}
        >
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="day" />
          <YAxis />
          <Line
            type="monotone"
            dataKey="cost"
            isAnimationActive={false}
            dot={false}
          />
        </LineChart>
      </ChartContainer>
    </figure>
  );
}
