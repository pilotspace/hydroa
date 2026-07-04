import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cn } from "@/lib/cn";

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  /**
   * Opt-in visual treatment (admin-console-ui, TASK.md §1 Framings weighed —
   * "Visual treatment... source"): `"flat"` renders a larger radius, no hard
   * border, and a softer/more diffuse shadow, matching the design-confirmed
   * platform-admin-console mock. ADDITIVE ONLY — omitted (or `"default"`)
   * renders BYTE-IDENTICAL classes to before this prop existed, so every one
   * of the 14 existing shipped pages that already render `Card` is unaffected.
   * `"flat"` is used only by this task's own new `components/platform/*`
   * screens — a system-wide rollout remains a separate, explicit future task.
   */
  variant?: "default" | "flat";
}

const Card = React.forwardRef<HTMLDivElement, CardProps>(
  ({ className, variant = "default", ...props }, ref) => (
    <div
      ref={ref}
      data-variant={variant}
      className={cn(
        "rounded-lg border border-border bg-card text-card-foreground shadow-md transition-shadow duration-200 ease-standard",
        variant === "flat" && "rounded-2xl border-transparent shadow-lg",
        className,
      )}
      {...props}
    />
  ),
);
Card.displayName = "Card";

const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("flex flex-col gap-1.5 p-6", className)} {...props} />
  ),
);
CardHeader.displayName = "CardHeader";

const CardTitle = React.forwardRef<
  HTMLHeadingElement,
  React.HTMLAttributes<HTMLHeadingElement> & { asChild?: boolean }
>(({ className, asChild = false, ...props }, ref) => {
  // asChild (Radix Slot) lets a consumer supply the heading element — e.g. <h2> — so a card can
  // keep a correct document outline (no level skip) while inheriting the CardTitle styling.
  // Default stays <h3>: byte-identical to every existing consumer.
  const Comp = asChild ? Slot : "h3";
  return (
    <Comp
      ref={ref}
      className={cn("text-lg font-semibold leading-none tracking-tight", className)}
      {...props}
    />
  );
});
CardTitle.displayName = "CardTitle";

const CardDescription = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLParagraphElement>>(
  ({ className, ...props }, ref) => (
    <p ref={ref} className={cn("text-sm text-muted-foreground", className)} {...props} />
  ),
);
CardDescription.displayName = "CardDescription";

const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("p-6 pt-0", className)} {...props} />
  ),
);
CardContent.displayName = "CardContent";

const CardFooter = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("flex items-center p-6 pt-0", className)} {...props} />
  ),
);
CardFooter.displayName = "CardFooter";

export { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter };
