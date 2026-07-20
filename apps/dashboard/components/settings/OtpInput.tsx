"use client";

/**
 * OtpInput — six single-character digit segments (member-verified-code-entry
 * TASK.md §3 CONTRACT item 5 — FROZEN @ v1).
 *
 * Digit-only, auto-advance, backspace-to-prev, paste-strip-and-fill. A
 * SEMI-controlled component: it owns its own segment state (so a stray
 * non-digit keystroke never round-trips through the parent) but reports every
 * change up via `onChange` so the parent can read the assembled code (for
 * Confirm) and force a reset by remounting via a changed `key` prop (the
 * parent's resend-clears-segments idiom).
 *
 * WCAG: `role="group"` `aria-label="6-digit confirmation code"` on the
 * container; each segment carries its own `aria-label` (position 1-of-6 etc.)
 * so a screen reader announces progress, never color/position alone.
 */

import { useRef, useState } from "react";

export interface OtpInputProps {
  length?: number;
  disabled?: boolean;
  /** Fires with the full segment array on every mutation (digit, backspace, paste). */
  onChange?: (segments: string[]) => void;
}

const DIGIT = /^[0-9]$/;

export function OtpInput({ length = 6, disabled = false, onChange }: OtpInputProps) {
  const [segments, setSegments] = useState<string[]>(() => Array(length).fill(""));
  const inputRefs = useRef<Array<HTMLInputElement | null>>([]);

  function commit(next: string[]) {
    setSegments(next);
    onChange?.(next);
  }

  function setSegment(i: number, char: string) {
    const next = [...segments];
    next[i] = char;
    commit(next);
  }

  function handleKeyDown(i: number, e: React.KeyboardEvent<HTMLInputElement>) {
    if (disabled) return;

    if (e.key === "Backspace") {
      e.preventDefault();
      if (segments[i]) {
        // filled -> clear in place
        setSegment(i, "");
      } else if (i > 0) {
        // empty -> backspace-to-prev: focus previous + clear it
        setSegment(i - 1, "");
        inputRefs.current[i - 1]?.focus();
      }
      return;
    }

    // Any other single printable character is handled here (not onChange) so a
    // rejected keystroke NEVER mutates state or advances focus — the segment
    // stays exactly as it was and no downstream handler (network, onChange) fires.
    if (e.key.length === 1) {
      e.preventDefault();
      if (!DIGIT.test(e.key)) return; // non-digit -> ignored, unchanged, no advance
      setSegment(i, e.key);
      if (i < length - 1) inputRefs.current[i + 1]?.focus();
    }
  }

  function handlePaste(i: number, e: React.ClipboardEvent<HTMLInputElement>) {
    if (disabled) return;
    e.preventDefault();
    const text = e.clipboardData.getData("text");
    const digits = text.replace(/\D/g, "").slice(0, length);
    if (digits.length === 0) return; // zero digits -> no-op, segments unchanged

    const next = [...segments];
    for (let idx = 0; idx < digits.length; idx++) {
      next[idx] = digits[idx];
    }
    commit(next);

    const focusIdx = digits.length >= length ? length - 1 : digits.length;
    inputRefs.current[focusIdx]?.focus();
  }

  return (
    <div role="group" aria-label="6-digit confirmation code" className="flex gap-2">
      {Array.from({ length }, (_, i) => (
        <input
          key={i}
          ref={(el) => {
            inputRefs.current[i] = el;
          }}
          value={segments[i]}
          onChange={() => {
            // Mutation is handled explicitly in onKeyDown/onPaste so every accepted
            // or rejected keystroke is deterministic; this is a required no-op for
            // a controlled <input>.
          }}
          onKeyDown={(e) => handleKeyDown(i, e)}
          onPaste={(e) => handlePaste(i, e)}
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={1}
          disabled={disabled}
          aria-label={`Digit ${i + 1} of ${length}`}
          className="size-11 rounded-md border border-border bg-background text-center font-mono text-lg tabular-nums text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        />
      ))}
    </div>
  );
}
