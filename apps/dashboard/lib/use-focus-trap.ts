"use client";

/**
 * useFocusTrap — accessible-dialog keyboard behavior for inline role="dialog" elements
 * (v13 key-budget-governance-ui). No portal, no dependency.
 *
 * While `active`, the returned ref's element:
 *   - moves initial focus to its first focusable child on activation,
 *   - traps Tab / Shift+Tab focus within itself (wraps at both ends),
 *   - closes on Escape (calls `onEscape`),
 *   - restores focus to the previously-focused element on deactivation.
 *
 * Deliberately conservative (the §1 ⚠): the keydown handler reacts ONLY to Tab and
 * Escape — it never intercepts clicks, typing, or any other key — so existing
 * userEvent flows (type into a field, click a button) are unaffected.
 */

import { useEffect, useRef } from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function useFocusTrap<T extends HTMLElement = HTMLDivElement>(
  active: boolean,
  onEscape?: () => void,
) {
  const containerRef = useRef<T | null>(null);
  // Keep the latest onEscape without re-subscribing the listener each render.
  const onEscapeRef = useRef(onEscape);
  onEscapeRef.current = onEscape;

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;

    const previouslyFocused = document.activeElement as HTMLElement | null;

    function focusables(): HTMLElement[] {
      if (!container) return [];
      // The selector already excludes disabled / hidden / tabindex=-1 controls.
      // (No offsetParent visibility filter — jsdom has no layout, so it would
      // drop every element under test.)
      return Array.from(
        container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
      );
    }

    // Move initial focus inside the dialog (first focusable, else the container).
    const initial = focusables()[0] ?? container;
    initial.focus();

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onEscapeRef.current?.();
        return;
      }
      if (e.key !== "Tab") return; // never touch typing / other keys
      const items = focusables();
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const activeEl = document.activeElement as HTMLElement | null;

      if (e.shiftKey) {
        if (activeEl === first || !container?.contains(activeEl)) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (activeEl === last || !container?.contains(activeEl)) {
          e.preventDefault();
          first.focus();
        }
      }
    }

    container.addEventListener("keydown", handleKeyDown);
    return () => {
      container.removeEventListener("keydown", handleKeyDown);
      // Restore focus to the opener when the dialog closes.
      if (previouslyFocused && typeof previouslyFocused.focus === "function") {
        previouslyFocused.focus();
      }
    };
  }, [active]);

  return containerRef;
}
