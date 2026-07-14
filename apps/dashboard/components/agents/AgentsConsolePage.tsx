"use client";

/**
 * AgentsConsolePage — agents-console TASK.md §3 CONTRACT (FROZEN @ v1). Orchestrator:
 * PageHeader + controlled Tabs (Directory default / Sessions / Policy); each tab owns
 * its own query state (Sessions/Policy panels are self-contained). Consumes THREE
 * FROZEN parent surfaces read-only: agent-identity-governance §3 (v2), mcp-connector-
 * passthrough §3 (v2), logs-explorer-api (unchanged, reused verbatim by the Sessions
 * tab). Every request goes through the existing `bffGet/bffPost` BFF client — no raw
 * gateway credential ever reaches the browser (M12, true by construction).
 *
 * Page-level gate (R1): the WHOLE page (not just Directory) waits on GET /admin/agents
 * before mounting the Tabs at all — a 403 (stale/direct URL below the nav's UX-only
 * role gate) renders one page-level ErrorState, never a partial/leaking render.
 */

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { bffGet, bffPost, BffError } from "@/lib/bff-client";
import { Button, Empty, ErrorState, Loading } from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useCurrentUser } from "@/lib/hooks/use-current-user";
import { AgentIdentityCard, type AgentPrincipal } from "./AgentIdentityCard";
import { CreateAgentDialog, type TenantUserOption } from "./CreateAgentDialog";
import { ManageTokensDialog } from "./ManageTokensDialog";
import { McpSessionsPanel } from "./McpSessionsPanel";
import { McpPolicyPanel } from "./McpPolicyPanel";

interface AgentsListResponse {
  agents: AgentPrincipal[];
}

interface UsersListResponse {
  users: { id: string; email: string }[];
}

interface ApiKeyBrief {
  key_id: string;
  name: string;
}

const AGENTS_QUERY_KEY = ["admin-agents"];

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError && err.status === 403) return "You don't have access to Agents";
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "Failed to load agents";
}

export function AgentsConsolePage() {
  const queryClient = useQueryClient();
  const { data: currentUser } = useCurrentUser();
  const role = currentUser?.role ?? null;

  const agentsQuery = useQuery<AgentsListResponse>({
    queryKey: AGENTS_QUERY_KEY,
    queryFn: () => bffGet<AgentsListResponse>("/admin/agents"),
    retry: false,
  });

  // Fetched in PARALLEL with agentsQuery (never gated behind it) — a sequential
  // agents-then-users/keys waterfall would delay owner-name/key-name resolution for
  // no reason; each query independently fails open (an unresolved id falls back to
  // its raw form) if it errors or simply hasn't settled yet.
  const usersQuery = useQuery<UsersListResponse>({
    queryKey: ["admin-users"],
    queryFn: () => bffGet<UsersListResponse>("/admin/users"),
    retry: false,
  });

  const keysQuery = useQuery<ApiKeyBrief[]>({
    queryKey: ["admin-keys"],
    queryFn: () => bffGet<ApiKeyBrief[]>("/admin/keys"),
    retry: false,
  });

  const usersById = useMemo(() => {
    const map: Record<string, string> = {};
    for (const u of usersQuery.data?.users ?? []) map[u.id] = u.email;
    return map;
  }, [usersQuery.data]);

  const users: TenantUserOption[] = useMemo(
    () => (usersQuery.data?.users ?? []).map((u) => ({ id: u.id, email: u.email })),
    [usersQuery.data],
  );

  const keys = useMemo(
    () => (keysQuery.data ?? []).map((k) => ({ id: k.key_id, label: k.name ?? k.key_id })),
    [keysQuery.data],
  );
  const keysById = useMemo(() => {
    const map: Record<string, string> = {};
    for (const k of keysQuery.data ?? []) map[k.key_id] = k.name ?? k.key_id;
    return map;
  }, [keysQuery.data]);

  const [createOpen, setCreateOpen] = useState(false);
  const [manageTokensId, setManageTokensId] = useState<string | null>(null);

  function resolveOwnerDisplayName(agent: AgentPrincipal): string | null {
    if (agent.owner_user_id === null) return null;
    return usersById[agent.owner_user_id] ?? agent.owner_user_id;
  }

  async function handleKill(id: string) {
    const resp = await bffPost<{ id: string; killed_at: string }>(`/admin/agents/${id}/kill`, {});
    queryClient.setQueryData<AgentsListResponse>(AGENTS_QUERY_KEY, (prev) =>
      prev
        ? { agents: prev.agents.map((a) => (a.id === id ? { ...a, killed_at: resp.killed_at } : a)) }
        : prev,
    );
  }

  function handleCreated(agent: AgentPrincipal) {
    queryClient.setQueryData<AgentsListResponse>(AGENTS_QUERY_KEY, (prev) =>
      prev ? { agents: [...prev.agents, agent] } : { agents: [agent] },
    );
  }

  const agents = agentsQuery.data?.agents ?? [];

  return (
    <section aria-labelledby="agents-heading" className="flex flex-col gap-6">
      <PageHeader
        title="Agents"
        titleId="agents-heading"
        description="Directory, session traces, and MCP policy for every agent principal."
        actions={
          <Button type="button" onClick={() => setCreateOpen(true)}>
            New agent
          </Button>
        }
      />

      {agentsQuery.isLoading ? (
        <Loading label="Loading agents…" />
      ) : agentsQuery.isError ? (
        <ErrorState title={getErrorTitle(agentsQuery.error)} />
      ) : (
        <Tabs defaultValue="directory">
          <TabsList>
            <TabsTrigger value="directory">Directory</TabsTrigger>
            <TabsTrigger value="sessions">Sessions</TabsTrigger>
            <TabsTrigger value="policy">Policy</TabsTrigger>
          </TabsList>

          <TabsContent value="directory">
            {agents.length === 0 ? (
              <Empty title="No agents yet" description="Create your first agent principal to get started." />
            ) : (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {agents.map((agent) => (
                  <AgentIdentityCard
                    key={agent.id}
                    agent={agent}
                    ownerDisplayName={resolveOwnerDisplayName(agent)}
                    onKill={handleKill}
                    onManageTokens={setManageTokensId}
                  />
                ))}
              </div>
            )}
          </TabsContent>

          <TabsContent value="sessions">
            <McpSessionsPanel keys={keys} keysById={keysById} />
          </TabsContent>

          <TabsContent value="policy">
            <McpPolicyPanel role={role} keys={keys} />
          </TabsContent>
        </Tabs>
      )}

      <CreateAgentDialog open={createOpen} onClose={() => setCreateOpen(false)} onCreated={handleCreated} users={users} />
      <ManageTokensDialog open={manageTokensId !== null} principalId={manageTokensId} onClose={() => setManageTokensId(null)} />
    </section>
  );
}
