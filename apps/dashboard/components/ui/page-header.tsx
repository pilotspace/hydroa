"use client";

import * as React from "react";
import { cn } from "@/lib/cn";

export interface PageHeaderProps {
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
  titleId?: string;
}

export function PageHeader({
  title,
  description,
  actions,
  className,
  titleId,
}: PageHeaderProps) {
  return (
    <header className={cn("flex flex-col gap-2", className)}>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1
          id={titleId}
          className="text-2xl font-semibold tracking-tight text-foreground"
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
    </header>
  );
}
