import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";

const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        outline: "border-border text-foreground",
        // status/semantic pills carry data → mono figures + tabular-nums (artifact `.pill`);
        // the word-label variants (default/secondary/outline) stay in the UI font.
        success: "border-transparent bg-success/10 text-success-text font-mono tabular-nums",
        warning: "border-transparent bg-warning/10 text-warning-foreground font-mono tabular-nums",
        destructive: "border-transparent bg-destructive/10 text-destructive-text font-mono tabular-nums",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
