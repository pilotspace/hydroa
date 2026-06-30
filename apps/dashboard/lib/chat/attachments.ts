/**
 * lib/chat/attachments.ts — the seam between a picked File and the wire.
 *
 * chat-attachments (chat-playground #4). Image attachments ride the existing
 * /v1/chat/completions as OpenAI content-parts — a base64 data-URL in
 * image_url.url. This module validates size + MIME BEFORE encoding
 * (design-for-failure: never build a multi-MB body for a file we will reject)
 * and returns a discriminated result. The per-message COUNT cap is the
 * composer's concern (it knows how many are already staged), not this function's.
 *
 * Pass-through: nothing here touches the gateway; the content-part shape is what
 * the wire already accepts.
 */

/** A single OpenAI message content part (the widened ChatMessage.content element). */
export type MessageContentPart =
  | { type: "text"; text: string }
  | { type: "image_url"; image_url: { url: string } };

/** A staged image attachment (in-memory, per live turn — not persisted in v1). */
export interface ImageAttachment {
  id: string;
  name: string;
  mime: string;
  /** "data:<mime>;base64,<…>" — what ships in image_url.url. */
  dataUrl: string;
  bytes: number;
}

export type AttachmentError = "attachment_too_large" | "attachment_unsupported_type";

export type AttachmentResult =
  | { ok: true; attachment: ImageAttachment }
  | { ok: false; error: AttachmentError };

/** Frozen defaults (chat-attachments v1): 4 × 5 MiB ≈ 27 MiB encoded < 32 MiB BFF cap. */
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
export const MAX_IMAGES_PER_MESSAGE = 4;
export const ALLOWED_MIME = ["image/png", "image/jpeg", "image/webp", "image/gif"] as const;

function newId(): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  return c?.randomUUID?.() ?? `att_${Math.random().toString(36).slice(2)}`;
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.readAsDataURL(file);
  });
}

/**
 * Validate (size, then MIME) and, if accepted, encode the file to a data-URL.
 * Size/type are checked BEFORE the read so a rejected file is never encoded.
 */
export async function fileToAttachment(file: File): Promise<AttachmentResult> {
  if (file.size > MAX_IMAGE_BYTES) return { ok: false, error: "attachment_too_large" };
  if (!(ALLOWED_MIME as readonly string[]).includes(file.type)) {
    return { ok: false, error: "attachment_unsupported_type" };
  }
  const dataUrl = await readAsDataUrl(file);
  return {
    ok: true,
    attachment: { id: newId(), name: file.name, mime: file.type, dataUrl, bytes: file.size },
  };
}

/** A human-readable reason for each rejection (shown in the composer). */
export function attachmentErrorMessage(error: AttachmentError): string {
  switch (error) {
    case "attachment_too_large":
      return `Image is too large (max ${Math.round(MAX_IMAGE_BYTES / (1024 * 1024))} MiB).`;
    case "attachment_unsupported_type":
      return "Unsupported file type — attach a PNG, JPEG, WebP, or GIF image.";
  }
}

/** The composer's count-cap message (over MAX_IMAGES_PER_MESSAGE). */
export const ATTACHMENT_LIMIT_MESSAGE = `You can attach up to ${MAX_IMAGES_PER_MESSAGE} images per message.`;

/**
 * Compose a user message's `content`: a plain string when there are no images
 * (byte-identical off path), else the OpenAI content-part array (text first).
 */
export function composeUserContent(
  text: string,
  images: ImageAttachment[] | undefined,
): string | MessageContentPart[] {
  if (!images || images.length === 0) return text;
  return [
    { type: "text", text },
    ...images.map((a): MessageContentPart => ({ type: "image_url", image_url: { url: a.dataUrl } })),
  ];
}

/** The text part of a (possibly multimodal) content — for copy/regenerate/markdown. */
export function contentText(content: string | MessageContentPart[]): string {
  if (typeof content === "string") return content;
  return content
    .filter((p): p is { type: "text"; text: string } => p.type === "text")
    .map((p) => p.text)
    .join("\n");
}

/** The image parts of a content (empty for a plain-string content). */
export function contentImages(content: string | MessageContentPart[]): string[] {
  if (typeof content === "string") return [];
  return content
    .filter((p): p is { type: "image_url"; image_url: { url: string } } => p.type === "image_url")
    .map((p) => p.image_url.url);
}
