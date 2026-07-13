"use client";

/**
 * TierSelector — residency-tiers-ui TASK.md §3 CONTRACT (FROZEN @ v1).
 *
 * Rendered inside CreateKeyDialog. Native `<input type="radio">` styled to match the
 * residency picker in RetentionZdrSettings (ONE shared visual treatment for "pick one
 * of a few options" — DESIGN.md §4). `priceDelta` is a server-derived string (NEVER a
 * hardcoded percentage) or null, rendered as "Pricing pending" (M10/R4).
 */

export type Tier = "priority" | "standard";

export interface TierSelectorProps {
  value: Tier;
  onChange: (next: Tier) => void;
  priceDelta: string | null;
}

const CAPACITY_COPY =
  "Priority requests get preference under contention and may fall back to Standard when capacity is unavailable — Standard is never starved.";

const RADIO_CLASS =
  "size-4 shrink-0 accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export function TierSelector({ value, onChange, priceDelta }: TierSelectorProps) {
  return (
    <fieldset className="flex flex-col gap-2 rounded-lg border border-border p-4">
      <legend className="px-1 text-sm font-semibold text-foreground">Service tier</legend>

      <div className="flex flex-col gap-2">
        <label className="flex min-h-11 items-center gap-2 text-sm text-foreground">
          <input
            type="radio"
            name="key-service-tier"
            value="standard"
            checked={value === "standard"}
            onChange={() => onChange("standard")}
            className={RADIO_CLASS}
          />
          Standard
        </label>

        <label className="flex min-h-11 items-center gap-2 text-sm text-foreground">
          <input
            type="radio"
            name="key-service-tier"
            value="priority"
            checked={value === "priority"}
            onChange={() => onChange("priority")}
            className={RADIO_CLASS}
          />
          Priority
          <span className="text-sm text-muted-foreground">{priceDelta ?? "Pricing pending"}</span>
        </label>
      </div>

      <p className="text-xs text-muted-foreground">{CAPACITY_COPY}</p>
    </fieldset>
  );
}
