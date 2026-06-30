"use client";

/**
 * components/chat/ParameterField.tsx — chat-parameters-panel control primitives.
 *
 * The inspector Parameters tab's live sampling controls. Each models an OPTIONAL
 * value: `undefined` means "unset" so the request body omits the key entirely
 * (the omitted-when-unset invariant — a default never leaks). Sliders show a
 * default position while unset; number inputs coerce + validate (invalid ⇒
 * undefined ⇒ omitted); the tag input caps entries; the segmented control is a
 * single-choice button group. All values are lifted to ChatWorkspace.
 */

import { useState, type KeyboardEvent } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";

function Row({
  label,
  children,
  value,
  note,
  disabled,
}: {
  label: string;
  children: React.ReactNode;
  value?: string;
  note?: string;
  disabled?: boolean;
}) {
  return (
    <div className={cn("flex flex-col gap-1.5", disabled && "opacity-60")}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-foreground">{label}</span>
        {value !== undefined ? (
          <span className="w-12 rounded-md border border-border bg-muted/40 px-1.5 py-0.5 text-right text-xs tabular-nums text-foreground">
            {value}
          </span>
        ) : null}
      </div>
      {children}
      {note ? <span className="text-[11px] italic text-muted-foreground">{note}</span> : null}
    </div>
  );
}

/** A labelled slider. `value` undefined = unset (omitted); the thumb sits at `defaultPos`. */
export function ParamSlider({
  label,
  value,
  defaultPos,
  min,
  max,
  step,
  onValue,
  disabled,
  note,
}: {
  label: string;
  value: number | undefined;
  defaultPos: number;
  min: number;
  max: number;
  step: number;
  onValue: (n: number) => void;
  disabled?: boolean;
  note?: string;
}) {
  const pos = value ?? defaultPos;
  return (
    <Row label={label} value={pos.toFixed(step < 1 ? 2 : 0)} note={note} disabled={disabled}>
      <input
        aria-label={label}
        type="range"
        min={min}
        max={max}
        step={step}
        value={pos}
        disabled={disabled}
        // Clamp to [min,max] before lifting: the HTML min/max only bound a real
        // pointer drag, not a programmatic set — the contract requires the value
        // is constrained before it can ever reach the request body.
        onChange={(e) => onValue(Math.min(max, Math.max(min, Number(e.target.value))))}
        className="w-full accent-primary disabled:cursor-not-allowed"
      />
    </Row>
  );
}

/** A labelled integer input. Empty/invalid ⇒ onValue(undefined) ⇒ the key is omitted. */
export function ParamNumber({
  label,
  value,
  onValue,
  min,
  placeholder,
  disabled,
  note,
}: {
  label: string;
  value: number | undefined;
  onValue: (n: number | undefined) => void;
  min?: number;
  placeholder?: string;
  disabled?: boolean;
  note?: string;
}) {
  return (
    <Row label={label} note={note} disabled={disabled}>
      <input
        aria-label={label}
        type="number"
        inputMode="numeric"
        min={min}
        placeholder={placeholder}
        disabled={disabled}
        value={value === undefined ? "" : String(value)}
        onChange={(e) => {
          const raw = e.target.value.trim();
          if (raw === "") return onValue(undefined);
          const n = Number.parseInt(raw, 10);
          if (!Number.isFinite(n) || (min !== undefined && n < min)) return onValue(undefined);
          onValue(n);
        }}
        className="w-full rounded-md border border-border bg-background px-2 py-1 text-sm tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:bg-muted/40"
      />
    </Row>
  );
}

/** A chip/tag input — Enter adds a trimmed, non-empty, non-duplicate entry up to `max`. */
export function ParamTags({
  label,
  values,
  onValues,
  max = 4,
  disabled,
  note,
}: {
  label: string;
  values: string[];
  onValues: (v: string[]) => void;
  max?: number;
  disabled?: boolean;
  note?: string;
}) {
  const [draft, setDraft] = useState("");
  function commit() {
    const v = draft.trim();
    if (!v || values.length >= max || values.includes(v)) {
      setDraft("");
      return;
    }
    onValues([...values, v]);
    setDraft("");
  }
  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      commit();
    }
  }
  return (
    <Row label={label} note={note} disabled={disabled}>
      <div className="flex flex-col gap-1.5">
        <input
          aria-label={label}
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={values.length >= max ? "Max 4" : "Type and press Enter"}
          disabled={disabled || values.length >= max}
          className="w-full rounded-md border border-border bg-background px-2 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:bg-muted/40"
        />
        {values.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {values.map((v) => (
              <span
                key={v}
                className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/50 px-2 py-0.5 text-xs text-foreground"
              >
                <span className="max-w-[8rem] truncate">{v}</span>
                <button
                  type="button"
                  aria-label={`Remove ${v}`}
                  onClick={() => onValues(values.filter((x) => x !== v))}
                  className="text-muted-foreground hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <X className="size-3" aria-hidden="true" />
                </button>
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </Row>
  );
}

/** A single-choice segmented button group (e.g. Response format: Text | JSON). */
export function ParamSegmented<T extends string>({
  label,
  value,
  options,
  onValue,
  disabled,
  note,
}: {
  label: string;
  value: T;
  options: ReadonlyArray<{ value: T; label: string }>;
  onValue: (v: T) => void;
  disabled?: boolean;
  note?: string;
}) {
  return (
    <Row label={label} note={note} disabled={disabled}>
      <div role="group" aria-label={label} className="flex rounded-md border border-border p-0.5">
        {options.map((opt) => {
          const active = opt.value === value;
          return (
            <button
              key={opt.value}
              type="button"
              aria-pressed={active}
              disabled={disabled}
              onClick={() => onValue(opt.value)}
              className={cn(
                "flex-1 rounded px-2 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed",
                active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {opt.label}
            </button>
          );
        })}
      </div>
    </Row>
  );
}
