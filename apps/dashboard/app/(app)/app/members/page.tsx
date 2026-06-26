"use client";

/**
 * /app/members — tenant user role-assignment page.
 *
 * Lists all users in the caller's tenant and exposes a role selector for each.
 * Requires MEMBERS_MANAGE (owner/admin). Lower roles get a 403 from the gateway
 * which surfaces as the standard ErrorState.
 *
 * The callerRole + callerUserId are resolved from the /api/auth/me identity
 * so the role selector can be filtered client-side (convenience; server enforces).
 */

import { useQuery } from "@tanstack/react-query";
import { bffAuthGet } from "@/lib/bff-client";
import { MembersPage } from "@/components/members/MembersPage";

interface MeResponse {
  user_id: string;
  tenant_id: string;
  email: string;
  role: string;
}

export default function MembersRoute() {
  const meQuery = useQuery<MeResponse>({
    queryKey: ["current-user"],
    queryFn: () => bffAuthGet<MeResponse>("me"),
  });

  return (
    <MembersPage
      callerRole={meQuery.data?.role ?? "member"}
      callerUserId={meQuery.data?.user_id}
    />
  );
}
