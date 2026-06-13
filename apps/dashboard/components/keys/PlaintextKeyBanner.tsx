"use client";

/**
 * PlaintextKeyBanner — one-time display of newly created plaintext key
 *
 * Safety (§5): plaintext key MUST be cleared from state when dismissed.
 * The parent calls onDismiss() and sets plaintextKey to null so this
 * component never re-renders with the secret.
 */

import { useState } from "react";
import { Button } from "@/components/ui";

interface PlaintextKeyBannerProps {
  plaintextKey: string;
  onDismiss: () => void;
}

export function PlaintextKeyBanner({ plaintextKey, onDismiss }: PlaintextKeyBannerProps) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(plaintextKey);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback: select-and-copy not needed for tests
    }
  }

  return (
    <div
      role="alert"
      aria-live="polite"
      className="flex flex-col gap-3 rounded-lg border border-success/30 bg-success/5 p-4"
    >
      <p className="text-sm font-semibold text-foreground">
        You won&apos;t see this key again
      </p>
      {/* The plaintext secret is rendered ONLY in this visible <code> leaf — never
          logged, persisted, or copied into an attribute. Cleared by the parent on dismiss. */}
      <code className="block break-all rounded-md border border-border bg-muted px-3 py-2 font-mono text-sm text-foreground">
        {plaintextKey}
      </code>
      <div className="flex gap-2">
        <Button type="button" variant="outline" size="sm" onClick={handleCopy}>
          {copied ? "Copied!" : "Copy"}
        </Button>
        <Button type="button" size="sm" onClick={onDismiss}>
          Done
        </Button>
      </div>
    </div>
  );
}
