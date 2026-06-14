"use client";

/**
 * TeamsPage — owner/admin teams governance surface (`/teams`).
 *
 * Master-detail on one page: a teams list (create / delete / set budget) plus a
 * members panel for the selected team. Consumes the frozen teams gateway contract
 * through the BFF seam (bffGet/bffPost/bffDelete). GET /admin/teams returns a BARE
 * array — no {object,data} envelope. Owner/admin-only on the backend: a member's
 * GET 403s, surfaced as the standard ErrorState (no client role gate). Data calls
 * never read or construct an Authorization header — the JWT lives in the BFF cookie.
 */

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { bffGet, bffPost, bffDelete, BffError } from "@/lib/bff-client";
import {
  Card,
  CardContent,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  Button,
  Loading,
  ErrorState,
  Empty,
} from "@/components/ui";
import type { TeamResponse } from "./types";
import { CreateTeamDialog } from "./CreateTeamDialog";
import { ConfirmDialog } from "./ConfirmDialog";
import { TeamBudgetForm } from "./TeamBudgetForm";
import { TeamMembersPanel } from "./TeamMembersPanel";

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

export function TeamsPage() {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<TeamResponse | null>(null);

  const { data, isLoading, isError, error } = useQuery<TeamResponse[]>({
    queryKey: ["admin-teams"],
    queryFn: () => bffGet<TeamResponse[]>("/admin/teams"),
  });

  const createTeam = useMutation({
    mutationFn: (name: string) => bffPost<TeamResponse>("/admin/teams", { name }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-teams"] });
    },
  });

  const deleteTeam = useMutation({
    mutationFn: (id: string) => bffDelete(`/admin/teams/${id}`),
    onSuccess: (_result, id) => {
      void queryClient.invalidateQueries({ queryKey: ["admin-teams"] });
      if (selectedId === id) setSelectedId(null);
    },
  });

  const teams = data ?? [];

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Teams</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Manage teams, monthly budgets, and members for your tenant.
          </p>
        </div>
        <Button onClick={() => setIsCreateOpen(true)}>Create team</Button>
      </header>

      {isLoading && <Loading label="Loading teams" className="animate-pulse" />}

      {isError && !isLoading && <ErrorState title={getErrorTitle(error)} />}

      {!isLoading && !isError && teams.length === 0 && (
        <Empty
          title="No teams yet"
          description="Create your first team to organize members and budgets."
        />
      )}

      {!isLoading && !isError && teams.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Team</TableHead>
                  <TableHead>Members</TableHead>
                  <TableHead>Keys</TableHead>
                  <TableHead>Monthly budget (USD)</TableHead>
                  <TableHead>
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {teams.map((team) => (
                  <TableRow key={team.id}>
                    <TableCell>
                      <button
                        type="button"
                        onClick={() => setSelectedId(team.id)}
                        aria-current={team.id === selectedId ? "true" : undefined}
                        className="rounded font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring aria-[current]:underline"
                      >
                        {team.name}
                      </button>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{team.member_count}</TableCell>
                    <TableCell className="text-muted-foreground">{team.key_count}</TableCell>
                    <TableCell>
                      <TeamBudgetForm team={team} />
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="destructive"
                        size="sm"
                        aria-label={`Delete team ${team.name}`}
                        onClick={() => setDeleteTarget(team)}
                      >
                        Delete
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {selectedId && <TeamMembersPanel teamId={selectedId} />}

      <CreateTeamDialog
        isOpen={isCreateOpen}
        onClose={() => setIsCreateOpen(false)}
        onSubmit={(name) => createTeam.mutateAsync(name).then(() => undefined)}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        title={deleteTarget ? `Delete team ${deleteTarget.name}` : ""}
        description="This permanently deletes the team and removes its members. Keys keep working but lose their team."
        confirmLabel="Delete"
        onClose={() => setDeleteTarget(null)}
        onConfirm={async () => {
          if (deleteTarget) await deleteTeam.mutateAsync(deleteTarget.id);
        }}
      />
    </div>
  );
}
