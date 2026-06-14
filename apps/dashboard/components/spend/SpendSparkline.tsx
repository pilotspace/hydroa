"use client";

/**
 * SpendSparkline — additive, decorative spend-over-time trend for SpendPage.
 *
 * Frozen §3 NEW CHART RENDER CONTRACT (v13 usage-cost-ui):
 *   - derives ONLY from props.buckets (no new fetch / query key / field)
 *   - wrapped in <figure data-testid="spend-chart" aria-label="Spend over time">
 *     with a visible <figcaption> title
 *   - NEVER the sole source of a datum: SpendPage still renders the bucket list +
 *     totals-* leaves as the accessible fallback
 *   - buckets.length === 0 -> render nothing (zero-state path stays unchanged)
 *
 * Implementation note (why fixed dims + no axes):
 *   We deliberately avoid <ResponsiveContainer> (it needs ResizeObserver, absent in
 *   the jsdom test setup) and omit <XAxis>/<YAxis> tick labels. Recharts axis ticks
 *   render SVG <text> nodes that would collide with the existing govern tests'
 *   getByText(/1\.23/) / getByText(/2026-06-01/) matchers. A pure sparkline emits no
 *   matchable text. Fluid/responsive sizing is the ui-ux-verify task's scope.
 */

import { useEffect } from "react";
import { LineChart, Line, CartesianGrid } from "recharts";

interface SpendBucketPoint {
  bucket_start: string;
  cost_usd: string;
}

export interface SpendSparklineProps {
  buckets: SpendBucketPoint[];
  /** logical chart size — visually constrained by the w-full wrapper */
  width?: number;
  height?: number;
}

export function SpendSparkline({
  buckets,
  width = 560,
  height = 160,
}: SpendSparklineProps) {
  // Recharts measures text width via an imperative <span id="recharts_measurement_span">
  // appended to document.body. RTL's cleanup() only unmounts React trees, so that span
  // leaks across tests and pollutes later getByText queries (e.g. /\b0\b/ matching a stray
  // "0.25" tick). Remove it on unmount — cleanup() unmounts us, so it is gone before the
  // next test renders.
  useEffect(() => {
    return () => {
      document.getElementById("recharts_measurement_span")?.remove();
    };
  }, []);

  if (buckets.length === 0) return null;

  const data = buckets.map((b) => ({
    bucket_start: b.bucket_start,
    cost: Number.parseFloat(b.cost_usd) || 0,
  }));

  return (
    <figure
      data-testid="spend-chart"
      aria-label="Spend over time"
      className="my-4 w-full overflow-x-auto rounded-lg border border-border bg-card p-4"
    >
      <figcaption className="mb-2 text-sm font-medium text-foreground">
        Spend over time
      </figcaption>
      <LineChart
        width={width}
        height={height}
        data={data}
        aria-hidden="true"
        role="presentation"
        // Decorative chart: SpendPage renders the bucket list + totals as the
        // accessible source. Recharts 3's accessibilityLayer (default on) adds
        // tabindex=0 to the surface, which on an aria-hidden element is a serious
        // axe violation (aria-hidden-focus). Disable it so the decorative chart
        // stays out of the tab order.
        accessibilityLayer={false}
      >
        <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
        <Line
          type="monotone"
          dataKey="cost"
          stroke="var(--color-primary)"
          strokeWidth={2}
          dot={false}
          isAnimationActive={false}
        />
      </LineChart>
    </figure>
  );
}
