"use client";

/**
 * GuardrailAnalyticsSparkline — additive, decorative evaluations-over-time trend for
 * GuardrailAnalyticsPage. Structural clone of components/spend/SpendSparkline.tsx
 * (guardrail-analytics TASK.md §3 M8: "mirrors SpendPage's exact IA").
 *
 * NEW CHART RENDER CONTRACT (mirrors the frozen v13 usage-cost-ui contract SpendSparkline
 * itself honors):
 *   - derives ONLY from props.buckets (no new fetch / query key / field)
 *   - wrapped in <figure data-testid="guardrail-analytics-chart" aria-label="Guardrail
 *     evaluations over time"> with a visible <figcaption> title
 *   - NEVER the sole source of a datum: GuardrailAnalyticsPage still renders the bucket
 *     list + totals-* leaves as the accessible fallback
 *   - buckets.length === 0 -> render nothing (zero-state path stays unchanged)
 *
 * Implementation note (why fixed dims + no axes): same as SpendSparkline — avoids
 * <ResponsiveContainer> (needs ResizeObserver, absent in jsdom) and axis tick <text>
 * nodes that would collide with getByText matchers.
 */

import { useEffect } from "react";
import { LineChart, Line, CartesianGrid } from "recharts";
import { ChartContainer, type ChartConfig } from "@/components/ui";

const CHART_CONFIG: ChartConfig = {
  evaluations: { label: "Evaluations", color: "var(--color-chart-1)" },
};

interface GuardrailAnalyticsBucketPoint {
  bucket_start: string;
  evaluations: number;
}

export interface GuardrailAnalyticsSparklineProps {
  buckets: GuardrailAnalyticsBucketPoint[];
  /** logical chart size — visually constrained by the w-full wrapper */
  width?: number;
  height?: number;
}

export function GuardrailAnalyticsSparkline({
  buckets,
  width = 560,
  height = 160,
}: GuardrailAnalyticsSparklineProps) {
  // Recharts measures text width via an imperative <span id="recharts_measurement_span">
  // appended to document.body. RTL's cleanup() only unmounts React trees, so that span
  // leaks across tests and pollutes later getByText queries. Remove it on unmount —
  // cleanup() unmounts us, so it is gone before the next test renders (mirrors
  // SpendSparkline's own precedent).
  useEffect(() => {
    return () => {
      document.getElementById("recharts_measurement_span")?.remove();
    };
  }, []);

  if (buckets.length === 0) return null;

  const data = buckets.map((b) => ({
    bucket_start: b.bucket_start,
    evaluations: b.evaluations,
  }));

  return (
    <figure
      data-testid="guardrail-analytics-chart"
      aria-label="Guardrail evaluations over time"
      className="my-4 w-full overflow-x-auto rounded-lg border border-border bg-card p-4"
    >
      <figcaption className="mb-2 text-sm font-medium text-foreground">
        Evaluations over time
      </figcaption>
      {/* Wrapped in the shared ChartContainer: publishes --color-evaluations from
          CHART_CONFIG and carries data-slot="chart". aspect-auto neutralises the
          container's default aspect-video so the fixed-dim sparkline keeps its size. */}
      <ChartContainer config={CHART_CONFIG} className="aspect-auto">
        <LineChart
          width={width}
          height={height}
          data={data}
          aria-hidden="true"
          role="presentation"
          // Decorative chart: GuardrailAnalyticsPage renders the bucket list + totals as
          // the accessible source. Recharts 3's accessibilityLayer (default on) adds
          // tabindex=0 to the surface, which on an aria-hidden element is a serious axe
          // violation (aria-hidden-focus). Disable it so the decorative chart stays out
          // of the tab order.
          accessibilityLayer={false}
        >
          <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
          <Line
            type="monotone"
            dataKey="evaluations"
            stroke="var(--color-evaluations)"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ChartContainer>
    </figure>
  );
}
