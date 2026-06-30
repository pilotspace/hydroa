/**
 * tests-bff/attachments.test.ts — RED unit suite for the chat-attachments seam.
 *
 * lib/chat/attachments.ts is the boundary between a picked File and the wire
 * data-URL: it validates size + MIME BEFORE encoding (design-for-failure) and
 * returns a discriminated result. The count cap is the composer's concern, not
 * this function's.
 *
 * RED before Build: lib/chat/attachments.ts does not exist yet.
 */

import { describe, it, expect } from "vitest";
import {
  fileToAttachment,
  MAX_IMAGE_BYTES,
  MAX_IMAGES_PER_MESSAGE,
  ALLOWED_MIME,
} from "@/lib/chat/attachments";

function pngFile(bytes = 3, name = "a.png"): File {
  return new File([new Uint8Array(bytes)], name, { type: "image/png" });
}

describe("fileToAttachment", () => {
  it("test_fileToAttachment_ok", async () => {
    const res = await fileToAttachment(pngFile(3));
    expect(res.ok).toBe(true);
    if (!res.ok) throw new Error("expected ok");
    expect(res.attachment.mime).toBe("image/png");
    expect(res.attachment.bytes).toBe(3);
    expect(res.attachment.name).toBe("a.png");
    expect(res.attachment.dataUrl).toMatch(/^data:image\/png;base64,/);
    expect(typeof res.attachment.id).toBe("string");
  });

  it("test_fileToAttachment_too_large", async () => {
    const big = new File([new Uint8Array(MAX_IMAGE_BYTES + 1)], "big.png", { type: "image/png" });
    const res = await fileToAttachment(big);
    expect(res.ok).toBe(false);
    if (res.ok) throw new Error("expected rejection");
    expect(res.error).toBe("attachment_too_large");
  });

  it("test_fileToAttachment_unsupported", async () => {
    const pdf = new File([new Uint8Array(3)], "a.pdf", { type: "application/pdf" });
    const res = await fileToAttachment(pdf);
    expect(res.ok).toBe(false);
    if (res.ok) throw new Error("expected rejection");
    expect(res.error).toBe("attachment_unsupported_type");
  });

  it("test_limit_constants_under_bff_cap", () => {
    // the frozen defaults: 4 × 5 MiB raw ≈ 27 MiB encoded < 32 MiB BFF cap
    expect(MAX_IMAGE_BYTES).toBe(5 * 1024 * 1024);
    expect(MAX_IMAGES_PER_MESSAGE).toBe(4);
    expect(ALLOWED_MIME).toEqual(["image/png", "image/jpeg", "image/webp", "image/gif"]);
  });
});
