/**
 * lib/vision.ts — v46 BFF client for the multimodal vision endpoint.
 *
 * All calls route through bff-client helpers (same-origin /api/gw/* with
 * credentials:"include"). Tenant id is NEVER sent from the client — the BFF
 * cookie-scopes all requests to the authenticated tenant automatically.
 *
 * askVision() sends a non-streaming multimodal chat/completions request with
 * a content array: a text part + either an image_url or video_url part,
 * depending on the media type. It returns the model's text response, or ""
 * on any shape mismatch (never throws on shape — optional-chaining + fallback).
 */

import { bffPost } from "@/lib/bff-client";

export type MediaKind = "image" | "video";

type ChatCompletion = {
  choices?: Array<{ message?: { content?: string } }>;
};

type ImagePart = {
  type: "image_url";
  image_url: { url: string };
};

type VideoPart = {
  type: "video_url";
  video_url: { url: string };
};

type TextPart = {
  type: "text";
  text: string;
};

/**
 * Send a multimodal prompt to the gateway via the BFF.
 *
 * @param args.model     — the Gemini model id to use
 * @param args.prompt    — text prompt
 * @param args.dataUrl   — data: URL from FileReader.readAsDataURL
 * @param args.mediaType — "image" | "video" — determines part type
 * @returns the model's text answer, or "" if the shape is unexpected
 */
export async function askVision(args: {
  model: string;
  prompt: string;
  dataUrl: string;
  mediaType: MediaKind;
}): Promise<string> {
  const mediaPart: ImagePart | VideoPart =
    args.mediaType === "video"
      ? { type: "video_url", video_url: { url: args.dataUrl } }
      : { type: "image_url", image_url: { url: args.dataUrl } };

  const textPart: TextPart = { type: "text", text: args.prompt };

  const body = {
    model: args.model,
    messages: [
      {
        role: "user",
        content: [textPart, mediaPart],
      },
    ],
    stream: false,
  };

  const res = await bffPost<ChatCompletion>("/v1/chat/completions", body);
  return res.choices?.[0]?.message?.content ?? "";
}
