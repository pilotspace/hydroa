"use client";

import * as React from "react";
import { cn } from "@/lib/cn";

export interface PageHeaderProps {
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
  titleId?: string;
  /**
   * Optional uppercase eyebrow rendered ABOVE the title (v7 premium). A `<span>`
   * (NOT a `<p>`) and a SIBLING of the title row — so the h1 stays a direct child
   * of the flex row (frozen governance-redesign structure) and a bare header still
   * emits no paragraph (frozen monitoring-redesign test).
   */
  eyebrow?: string;
  /**
   * Optional monospace "spec-sheet" metadata strip rendered BELOW the header
   * (v7 premium — the mock's hairline ref-tag: Index · Scope · Access, etc.).
   * Additive; every existing caller omits it and renders byte-identically.
   */
  meta?: React.ReactNode;
}

export function PageHeader({
  title,
  description,
  actions,
  className,
  titleId,
  eyebrow,
  meta,
}: PageHeaderProps) {
  return (
    <header className={cn("flex flex-col gap-2", className)}>
      {eyebrow ? (
        <span className="text-caption font-semibold uppercase tracking-[0.1em] text-muted-foreground">
          {eyebrow}
        </span>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1
          id={titleId}
          className="text-display font-semibold tracking-tight text-foreground"
        >
          {title}
        </h1>
        {actions && (
          <div className="flex flex-wrap items-center gap-2">
            {actions}
          </div>
        )}
      </div>
      {description && (
        <p className="text-sm text-muted-foreground">{description}</p>
      )}
      {meta ? (
        <div className="mt-1 flex flex-wrap gap-x-8 gap-y-1 border-y border-border py-2.5 font-mono text-caption text-muted-foreground">
          {meta}
        </div>
      ) : null}
    </header>
  );
}
