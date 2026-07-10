import * as React from "react";
import { Loader2, Inbox, AlertCircle, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * Standard state components — the v13 realization of the v1 UDD "state pattern"
 * (loading · empty · error · success rendered identically across every surface).
 * The observable markers are part of the frozen contract:
 *   Loading    -> role="status" + aria-busy="true" + an animate-spin marker (T10/T23 fallback)
 *   ErrorState -> role="alert" with the caller's title verbatim
 * Copy is ALWAYS caller-supplied so surfaces keep their exact, test-asserted text.
 */

export interface LoadingProps extends React.HTMLAttributes<HTMLDivElement> {
  label?: string;
}

export function Loading({ label = "Loading…", className, ...props }: LoadingProps) {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label={label}
      className={cn("flex items-center gap-2 text-sm text-muted-foreground", className)}
      {...props}
    >
      <Loader2 className="size-4 animate-spin" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export interface EmptyProps {
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}

export function Empty({ title, description, action, className }: EmptyProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border p-8 text-center",
        className,
      )}
    >
      <Inbox className="size-6 text-muted-foreground" aria-hidden="true" />
      <p className="text-sm font-medium text-foreground">{title}</p>
      {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
      {action}
    </div>
  );
}

export interface ErrorStateProps {
  title: string;
  description?: string;
  onRetry?: () => void;
  className?: string;
  /**
   * Element for the title. Default "p" — most callers render ErrorState INSIDE a page that
   * already owns the sole h1, so a heading here would create a second one (breaking the
   * dashboard-wide "exactly one h1" contract). Error BOUNDARIES that REPLACE the whole page
   * segment (RouteError) pass "h1" so the alert carries a real, orienting heading (a11y sweep).
   */
  titleAs?: "h1" | "h2" | "p";
}

export function ErrorState({ title, description, onRetry, className, titleAs = "p" }: ErrorStateProps) {
  const TitleTag = titleAs;
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-destructive-text",
        className,
      )}
    >
      <div className="flex items-center gap-2">
        <AlertCircle className="size-4" aria-hidden="true" />
        <TitleTag className="text-sm font-medium">{title}</TitleTag>
      </div>
      {description ? <p className="text-sm text-foreground">{description}</p> : null}
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="self-start text-sm font-medium underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          Retry
        </button>
      ) : null}
    </div>
  );
}

export interface SuccessProps {
  title: string;
  description?: string;
  className?: string;
}

export function Success({ title, description, className }: SuccessProps) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-lg border border-success/30 bg-success/5 p-3 text-success-text",
        className,
      )}
    >
      <CheckCircle2 className="size-4" aria-hidden="true" />
      <div>
        <p className="text-sm font-medium">{title}</p>
        {description ? <p className="text-sm text-foreground">{description}</p> : null}
      </div>
    </div>
  );
}
