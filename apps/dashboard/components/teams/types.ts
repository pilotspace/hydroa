/**
 * components/teams/types.ts — client mirrors of the frozen teams gateway contract.
 *
 * Field names + types are byte-identical to apps/gateway/src/gateway/teams/api/schemas.py.
 * team_budget_usd is a decimal-as-STRING (nullable); GET /admin/teams returns a BARE
 * array (the BFF passes gateway JSON through verbatim — no {object,data} envelope).
 */

export interface TeamResponse {
  id: string;
  name: string;
  tenant_id: string;
  created_at: string;
  member_count: number;
  key_count: number;
  team_budget_usd: string | null;
}

export interface MemberResponse {
  user_id: string;
  role: string;
  added_at: string;
}

export interface TeamDetailResponse extends TeamResponse {
  members: MemberResponse[];
}

export interface AddMemberResponse {
  team_id: string;
  user_id: string;
  role: string;
  added_at: string;
}

export type TeamRole = "lead" | "member";
