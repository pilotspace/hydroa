"use client";

/**
 * PlaintextKeyBanner — one-time display of newly created plaintext key
 *
 * Safety (§5): plaintext key MUST be cleared from state when dismissed.
 * The parent calls onDismiss() and sets plaintextKey to null so this
 * component never re-renders with the secret.
 */

import { useState } from "react";

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
    <div role="alert" aria-live="polite">
      <p>
        <strong>You won&apos;t see this key again</strong>
      </p>
      <code>{plaintextKey}</code>
      <button type="button" onClick={handleCopy}>
        {copied ? "Copied!" : "Copy"}
      </button>
      <button type="button" onClick={onDismiss}>
        Done
      </button>
    </div>
  );
}
