/**
 * components/members/types.ts — client mirrors of the frozen invites gateway
 * contract (member-invite-ui):
 *
 *   POST   /admin/invites                    {email, role} -> 201 InviteCreateResponse
 *   GET    /admin/invites                                  -> {invites: PendingInviteListItem[]}
 *   DELETE /admin/invites/{invite_id}                       -> 204/200
 *
 * Field names are byte-identical to the frozen backend contract. GET /admin/invites
 * items never carry `token` — it is returned ONCE, on creation, and is never
 * retrievable again (that's why InviteCreateResponse and PendingInviteListItem are
 * separate shapes rather than one type with an optional field).
 */

export interface InviteCreateResponse {
  id: string;
  email: string;
  role: string;
  status: string;
  expires_at: string;
  created_at: string;
  invited_by_user_id: string;
  /** PLAINTEXT invite token — present ONLY on this 201 response, never again. */
  token: string;
  /**
   * Which EmailSender adapter the accept-link email was DISPATCHED to (attempted, not
   * confirmed-delivered — fire-and-forget can't know delivery outcome by response time).
   * Additive + optional for forward/backward safety (transactional-email TASK.md §3).
   */
  email_delivery_channel?: "smtp" | "console";
}

export interface PendingInviteListItem {
  id: string;
  email: string;
  role: string;
  status: string;
  expires_at: string;
  created_at: string;
  invited_by_user_id: string;
}

export interface InvitesListResponse {
  invites: PendingInviteListItem[];
}
