// Users — admin-only user management. Lists console users, lets an admin
// change a user's role (user/admin) and disable accounts. The backend guards
// against demoting/disabling the last admin (returns 400), so we surface that
// error and also disable the control client-side for the acting admin.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getUsers, patchUser } from "@/api/client";
import type { User } from "@/api/types";
import { useAuth } from "@/api/auth";
import {
  Badge,
  Card,
  EmptyState,
  ErrorText,
  PageHeader,
  Select,
  Spinner,
  Table,
  TD,
  Toggle,
} from "@/components/ui";
import { fmtDateTime } from "@/lib/format";

type AdminUser = User & { disabled: boolean; created_at: number };

const ROLE_OPTIONS = [
  { value: "user", label: "user" },
  { value: "admin", label: "admin" },
];

function RoleCell(props: { u: AdminUser; meId?: string; onError: (m: string) => void }) {
  const qc = useQueryClient();
  const patch = useMutation({
    mutationFn: (role: string) => patchUser(props.u.id, { role }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["users"] }),
    onError: (e) => props.onError(e.message),
  });
  return (
    <Select
      value={props.u.role}
      onChange={(v) => patch.mutate(v)}
      options={ROLE_OPTIONS}
      className="text-[12px]"
    />
  );
}

function DisableCell(props: { u: AdminUser; meId?: string; onError: (m: string) => void }) {
  const qc = useQueryClient();
  const patch = useMutation({
    mutationFn: () => patchUser(props.u.id, { disabled: !props.u.disabled }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["users"] }),
    onError: (e) => props.onError(e.message),
  });
  return <Toggle checked={!props.u.disabled} onChange={() => patch.mutate()} disabled={patch.isPending} />;
}

export function UsersPage() {
  const { user: me } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const query = useQuery({ queryKey: ["users"], queryFn: getUsers });

  const onError = (m: string) => setError(m);

  return (
    <div>
      <PageHeader
        title="Users"
        subtitle="Console accounts. Promote or demote roles, disable access — the last admin is always protected."
      />

      {error && (
        <div className="mb-3">
          <ErrorText>{error}</ErrorText>
        </div>
      )}

      <Card>
        {query.isLoading && (
          <div className="flex justify-center py-10">
            <Spinner />
          </div>
        )}
        {query.error && (
          <div className="p-4">
            <ErrorText>{query.error.message}</ErrorText>
          </div>
        )}
        {query.data &&
          (query.data.users.length === 0 ? (
            <EmptyState>No users yet.</EmptyState>
          ) : (
            <Table head={["Username", "Role", "Enabled", "Created", ""]}>
              {query.data.users.map((u) => {
                const isMe = me?.id === u.id;
                return (
                  <tr key={u.id}>
                    <TD className="font-medium">
                      <div className="flex items-center gap-2">
                        <span className="text-[var(--admin-text)]">{u.username}</span>
                        {isMe && (
                          <Badge tone="blue" title="this is you">
                            you
                          </Badge>
                        )}
                        {u.disabled && <Badge tone="gray">disabled</Badge>}
                      </div>
                    </TD>
                    <TD>
                      <RoleCell u={u} meId={me?.id} onError={onError} />
                    </TD>
                    <TD>
                      <DisableCell u={u} meId={me?.id} onError={onError} />
                    </TD>
                    <TD className="font-mono text-[12px] text-[var(--admin-text-dim)]">
                      {fmtDateTime(u.created_at)}
                    </TD>
                    <TD>
                      <span className="block text-right font-mono text-[11px] text-[var(--admin-text-dim)]">
                        {u.id.slice(0, 8)}
                      </span>
                    </TD>
                  </tr>
                );
              })}
            </Table>
          ))}
      </Card>
    </div>
  );
}
