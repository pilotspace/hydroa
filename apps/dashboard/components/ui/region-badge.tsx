import { Badge } from "./badge";

/**
 * RegionBadge — residency-tiers-ui TASK.md §3 CONTRACT (FROZEN @ v1).
 *
 * The ONE new shared visual this task introduces (milestone instruction: "design it
 * once, use everywhere") — `Badge variant="outline"` (a neutral descriptor, not a
 * status), text = `region.toUpperCase()`. Used in the catalog table (M6) and nowhere
 * else with a different visual treatment.
 */
export type Region = "us" | "eu" | "ap" | "global";

export interface RegionBadgeProps {
  region: Region;
  className?: string;
}

export function RegionBadge({ region, className }: RegionBadgeProps) {
  return (
    <Badge variant="outline" className={className}>
      {region.toUpperCase()}
    </Badge>
  );
}
