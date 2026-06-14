"use client";

import * as React from "react";
import { cn } from "@/lib/cn";

/**
 * Hand-rolled WAI-ARIA Tabs (native + ARIA, no dependency, no polyfill).
 * Automatic activation: arrow/Home/End move focus AND select. Roving tabindex:
 * only the active trigger is tabbable. Deterministic ids per value pair the
 * trigger's aria-controls with the panel's aria-labelledby.
 */
interface TabsContextValue {
  value: string;
  setValue: (v: string) => void;
  baseId: string;
}

const TabsContext = React.createContext<TabsContextValue | null>(null);

function useTabs(component: string): TabsContextValue {
  const ctx = React.useContext(TabsContext);
  if (!ctx) throw new Error(`${component} must be used within <Tabs>`);
  return ctx;
}

const tabId = (baseId: string, value: string) => `${baseId}-tab-${value}`;
const panelId = (baseId: string, value: string) => `${baseId}-panel-${value}`;

export interface TabsProps {
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  children: React.ReactNode;
  className?: string;
}

function Tabs({ value, defaultValue, onValueChange, children, className }: TabsProps) {
  const isControlled = value !== undefined;
  const [internal, setInternal] = React.useState(defaultValue ?? "");
  const current = isControlled ? value : internal;
  const baseId = React.useId();

  const setValue = React.useCallback(
    (next: string) => {
      if (!isControlled) setInternal(next);
      onValueChange?.(next);
    },
    [isControlled, onValueChange],
  );

  return (
    <TabsContext.Provider value={{ value: current, setValue, baseId }}>
      <div className={className}>{children}</div>
    </TabsContext.Provider>
  );
}

export interface TabsListProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode;
}

const TabsList = React.forwardRef<HTMLDivElement, TabsListProps>(
  ({ className, onKeyDown, children, ...props }, ref) => {
    const { setValue } = useTabs("TabsList");

    function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
      // Horizontal tablist (WAI-ARIA APG): Left/Right move between tabs, Home/End
      // jump to first/last. Up/Down are intentionally NOT tab navigation here
      // (they belong to a vertical tablist / moving focus into the panel).
      const keys = ["ArrowRight", "ArrowLeft", "Home", "End"];
      if (keys.includes(e.key)) {
        const tabs = Array.from(
          e.currentTarget.querySelectorAll<HTMLButtonElement>(
            '[role="tab"]:not([disabled])',
          ),
        );
        const idx = tabs.indexOf(document.activeElement as HTMLButtonElement);
        if (idx !== -1) {
          let next = idx;
          if (e.key === "ArrowRight") next = (idx + 1) % tabs.length;
          else if (e.key === "ArrowLeft") next = (idx - 1 + tabs.length) % tabs.length;
          else if (e.key === "Home") next = 0;
          else if (e.key === "End") next = tabs.length - 1;
          e.preventDefault();
          const target = tabs[next];
          target.focus();
          const v = target.getAttribute("data-value");
          if (v) setValue(v);
        }
      }
      onKeyDown?.(e);
    }

    return (
      <div
        ref={ref}
        role="tablist"
        onKeyDown={handleKeyDown}
        className={cn(
          "inline-flex items-center gap-1 border-b border-border",
          className,
        )}
        {...props}
      >
        {children}
      </div>
    );
  },
);
TabsList.displayName = "TabsList";

export interface TabsTriggerProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  value: string;
}

const TabsTrigger = React.forwardRef<HTMLButtonElement, TabsTriggerProps>(
  ({ value, className, onClick, ...props }, ref) => {
    const { value: active, setValue, baseId } = useTabs("TabsTrigger");
    const isActive = active === value;
    return (
      <button
        ref={ref}
        type="button"
        role="tab"
        id={tabId(baseId, value)}
        data-value={value}
        aria-selected={isActive}
        aria-controls={panelId(baseId, value)}
        tabIndex={isActive ? 0 : -1}
        onClick={(e) => {
          setValue(value);
          onClick?.(e);
        }}
        className={cn(
          "-mb-px inline-flex items-center whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50",
          isActive
            ? "border-primary text-foreground"
            : "border-transparent text-muted-foreground hover:text-foreground",
          className,
        )}
        {...props}
      />
    );
  },
);
TabsTrigger.displayName = "TabsTrigger";

export interface TabsContentProps extends React.HTMLAttributes<HTMLDivElement> {
  value: string;
}

const TabsContent = React.forwardRef<HTMLDivElement, TabsContentProps>(
  ({ value, className, children, ...props }, ref) => {
    const { value: active, baseId } = useTabs("TabsContent");
    if (active !== value) return null;
    return (
      <div
        ref={ref}
        role="tabpanel"
        id={panelId(baseId, value)}
        aria-labelledby={tabId(baseId, value)}
        tabIndex={0}
        className={cn(
          "mt-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          className,
        )}
        {...props}
      >
        {children}
      </div>
    );
  },
);
TabsContent.displayName = "TabsContent";

export { Tabs, TabsList, TabsTrigger, TabsContent };
