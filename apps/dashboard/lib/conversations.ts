/**
 * lib/conversations.ts — v43 typed BFF client for conversation history.
 *
 * All calls route through bff-client helpers (same-origin /api/gw/* with
 * credentials:"include"). Tenant id is NEVER sent from the client — the BFF
 * cookie-scopes all requests to the authenticated tenant automatically.
 */

import { bffGet, bffPost, bffPatch, bffDelete } from "@/lib/bff-client";
import type { ChatRole } from "@/lib/hooks/use-chat-stream";

export interface ConversationSummary {
  id: string;
  title: string | null;
  updated_at: string;
  message_count: number;
}

export interface ConversationDetail {
  id: string;
  title: string | null;
  messages: Array<{ role: ChatRole; content: string }>;
}

/** GET /v1/conversations — list all conversations for the authenticated tenant. */
export function listConversations(): Promise<{ data: ConversationSummary[] }> {
  return bffGet<{ data: ConversationSummary[] }>("/v1/conversations");
}

/** POST /v1/conversations — create a new conversation, optionally with a title. */
export function createConversation(
  title?: string,
): Promise<{ id: string; title: string | null }> {
  return bffPost<{ id: string; title: string | null }>("/v1/conversations", {
    title: title ?? null,
  });
}

/** GET /v1/conversations/:id — fetch a conversation with its full message history. */
export function getConversation(id: string): Promise<ConversationDetail> {
  return bffGet<ConversationDetail>(`/v1/conversations/${id}`);
}

/** POST /v1/conversations/:id/messages — append a single message to a conversation. */
export function appendMessage(
  id: string,
  role: ChatRole,
  content: string,
): Promise<unknown> {
  return bffPost<unknown>(`/v1/conversations/${id}/messages`, { role, content });
}

/** PATCH /v1/conversations/:id — rename a conversation title. */
export function renameConversation(
  id: string,
  title: string,
): Promise<{ id: string; title: string | null; created_at: string; updated_at: string }> {
  return bffPatch<{
    id: string;
    title: string | null;
    created_at: string;
    updated_at: string;
  }>(`/v1/conversations/${id}`, { title });
}

/** DELETE /v1/conversations/:id — permanently delete a conversation. */
export function deleteConversation(id: string): Promise<void> {
  return bffDelete(`/v1/conversations/${id}`);
}
