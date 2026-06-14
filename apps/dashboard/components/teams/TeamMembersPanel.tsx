"use client";

/**
 * TeamMembersPanel — the detail half of the /teams master-detail. Loads a selected
 * team's members (GET /admin/teams/{id}) and supports add-by-email
 * (POST .../members) + remove (DELETE .../members/{user_id}). Each mutation
 * invalidates the ["admin-team", teamId] query so the panel refetches. Renders the
 * four state patterns (Loading/Empty/Error/data) independently of the list.
 */

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { bffGet, bffPost, bffDelete, BffError } from "@/lib/bff-client";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  Badge,
  Button,
  Loading,
  ErrorState,
  Empty,
} from "@/components/ui";
import type { TeamDetailResponse, MemberResponse, TeamRole } from "./types";
import { AddMemberDialog } from "./AddMemberDialog";
import { ConfirmDialog } from "./ConfirmDialog";

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

interface TeamMembersPanelProps {
  teamId: string;
}

export function TeamMembersPanel({ teamId }: TeamMembersPanelProps) {
  const queryClient = useQueryClient();
  const [isAddOpen, setIsAddOpen] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<MemberResponse | null>(null);

  const { data, isLoading, isError, error } = useQuery<TeamDetailResponse>({
    queryKey: ["admin-team", teamId],
    queryFn: () => bffGet<TeamDetailResponse>(`/admin/teams/${teamId}`),
    enabled: teamId != null,
  });

  const addMember = useMutation({
    mutationFn: ({ email, role }: { email: string; role: TeamRole }) =>
      bffPost(`/admin/teams/${teamId}/members`, { email, role }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-team", teamId] });
    },
  });

  const removeMember = useMutation({
    mutationFn: (userId: string) =>
      bffDelete(`/admin/teams/${teamId}/members/${userId}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-team", teamId] });
    },
  });

  const members = data?.members ?? [];

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-4">
        <CardTitle>{data ? `Members of ${data.name}` : "Members"}</CardTitle>
        <Button size="sm" onClick={() => setIsAddOpen(true)}>
          Add member
        </Button>
      </CardHeader>
      <CardContent className="p-0">
        {isLoading && (
          <div className="p-6">
            <Loading label="Loading members" className="animate-pulse" />
          </div>
        )}

        {isError && !isLoading && (
          <div className="p-6">
            <ErrorState title={getErrorTitle(error)} />
          </div>
        )}

        {!isLoading && !isError && members.length === 0 && (
          <div className="p-6">
            <Empty title="No members yet" description="Add a teammate by their email address." />
          </div>
        )}

        {!isLoading && !isError && members.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>User</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Added</TableHead>
                <TableHead>
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.map((m) => (
                <TableRow key={m.user_id}>
                  <TableCell className="font-medium text-foreground">{m.user_id}</TableCell>
                  <TableCell>
                    <Badge variant="secondary">{m.role}</Badge>
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {new Date(m.added_at).toLocaleDateString()}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`Remove ${m.user_id}`}
                      onClick={() => setRemoveTarget(m)}
                    >
                      Remove
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>

      <AddMemberDialog
        isOpen={isAddOpen}
        onClose={() => setIsAddOpen(false)}
        onSubmit={(email, role) => addMember.mutateAsync({ email, role }).then(() => undefined)}
      />

      <ConfirmDialog
        open={removeTarget !== null}
        title={removeTarget ? `Remove ${removeTarget.user_id}` : ""}
        description="This removes the member from the team."
        confirmLabel="Remove"
        onClose={() => setRemoveTarget(null)}
        onConfirm={async () => {
          if (removeTarget) await removeMember.mutateAsync(removeTarget.user_id);
        }}
      />
    </Card>
  );
}
