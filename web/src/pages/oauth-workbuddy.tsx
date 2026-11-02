// WorkBuddy OAuth panel — the browser-login flow plus the connected-account
// table, embedded in /console/oauth alongside the Cline and Hermes panels.
//
// The login machine and the table already live in `workbuddy-shared`; this
// module only composes them for the accounts hub. Bulk import/export stays on
// the dedicated WorkBuddy page (linked from the sign-in card) because those
// are not OAuth flows and would crowd the hub.

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Briefcase } from "lucide-react";
import { getProviders, workbuddyAccounts } from "@/api/client";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorText,
  Spinner,
} from "@/components/ui";
import {
  WorkBuddyAccountsTable,
  WorkBuddyLoginForm,
  useWorkBuddyLogin,
} from "@/pages/workbuddy-shared";
import { SharedModelsCard } from "@/pages/oauth-models";

export function WorkBuddyOAuthPanel() {
  const [error, setError] = useState<string | null>(null);
  // The hook invalidates ["workbuddy-accounts"] and ["providers"] itself when
  // the account lands, so the table below refreshes on its own.
  const login = useWorkBuddyLogin();

  const accountsQ = useQuery({
    queryKey: ["workbuddy-accounts"],
    queryFn: workbuddyAccounts,
    refetchInterval: 15_000,
  });
  const providersQ = useQuery({
    queryKey: ["providers"],
    queryFn: getProviders,
    refetchInterval: 15_000,
  });
  const accounts = accountsQ.data?.accounts ?? [];
  const providers = useMemo(
    () => (providersQ.data?.providers ?? []).filter((p) => p.provider_type === "workbuddy"),
    [providersQ.data],
  );

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title={
            <span className="flex items-center gap-2">
              <Briefcase size={15} className="shrink-0 text-[var(--admin-text-muted)]" aria-hidden />
              WorkBuddy sign-in
            </span>
          }
          subtitle="Browser login against the CN or global WorkBuddy API"
          right={
            <Link to="/console/workbuddy" className="inline-flex">
              <Button variant="ghost" className="min-h-11">
                Import / export <ArrowRight size={13} aria-hidden />
              </Button>
            </Link>
          }
        />
        <div className="px-4 pb-4 pt-2">
          <WorkBuddyLoginForm state={login} />
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Connected accounts"
          subtitle="Every pool key wiwi holds for WorkBuddy"
          right={
            <Badge tone={accounts.length > 0 ? "green" : "gray"}>
              {accounts.length} account{accounts.length !== 1 ? "s" : ""}
            </Badge>
          }
        />
        <div className="px-4 pb-4 pt-2">
          {error && <div className="mb-3"><ErrorText>{error}</ErrorText></div>}
          {accountsQ.isLoading ? (
            <Spinner />
          ) : accountsQ.isError ? (
            <ErrorText>
              Failed to load accounts: {accountsQ.error?.message ?? "request failed"}
            </ErrorText>
          ) : accounts.length === 0 ? (
            <EmptyState>
              No WorkBuddy accounts yet. Start a sign-in above — the account
              appears here as soon as it is approved.
            </EmptyState>
          ) : (
            <WorkBuddyAccountsTable accounts={accounts} onError={setError} />
          )}
        </div>
      </Card>

      <SharedModelsCard
        providerType="workbuddy"
        prefix="workbuddy"
        label="WorkBuddy"
        accountCount={accounts.length}
        providers={providers}
        onError={setError}
      />
    </div>
  );
}
