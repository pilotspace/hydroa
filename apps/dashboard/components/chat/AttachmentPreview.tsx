/**
 * AttachmentPreview.tsx — image thumbnails for chat-attachments (chat-playground #4).
 *
 * Two surfaces:
 *  - <StagedAttachments> — the not-yet-sent strip in the composer, each thumbnail
 *    individually removable.
 *  - <MessageImages> — the read-only thumbnails inside a committed user turn.
 *
 * Both alt-text every image (a11y by construction). Thumbnails render the data-URL
 * directly (no object-URL lifecycle to manage).
 */

"use client";

import { X } from "lucide-react";
import type { ImageAttachment } from "@/lib/chat/attachments";
import { cn } from "@/lib/cn";

const THUMB = "size-16 rounded-lg border border-border object-cover";

export function StagedAttachments({
  items,
  onRemove,
}: {
  items: ImageAttachment[];
  onRemove: (id: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-2" aria-label="Staged attachments">
      {items.map((a) => (
        <li key={a.id} className="relative">
          {/* eslint-disable-next-line @next/next/no-img-element -- data-URL preview, no remote opt needed */}
          <img src={a.dataUrl} alt={a.name} className={THUMB} />
          <button
            type="button"
            onClick={() => onRemove(a.id)}
            aria-label={`Remove ${a.name}`}
            className="absolute -right-1.5 -top-1.5 inline-flex size-5 items-center justify-center rounded-full border border-border bg-background text-muted-foreground shadow-sm transition-colors hover:bg-destructive hover:text-destructive-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="size-3" aria-hidden="true" />
          </button>
        </li>
      ))}
    </ul>
  );
}

export function MessageImages({
  images,
  className,
}: {
  images: Array<{ url: string; alt: string }>;
  className?: string;
}) {
  if (images.length === 0) return null;
  return (
    <div className={cn("flex flex-wrap gap-2", className)}>
      {images.map((img, i) => (
        // eslint-disable-next-line @next/next/no-img-element -- data-URL attachment, no remote opt needed
        <img key={`${img.url.slice(0, 24)}-${i}`} src={img.url} alt={img.alt} className={THUMB} />
      ))}
    </div>
  );
}
