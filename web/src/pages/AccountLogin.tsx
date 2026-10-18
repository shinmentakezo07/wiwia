// Account login — an always-visible sign-in section for provider accounts.
//
// The sidebar points here rather than at the WorkBuddy page's modal because a
// sign-in is a task in its own right: the operator opens this page, starts the
// login, approves it in the provider's browser tab, and wiwi saves the account
// with every detail the provider returned — provider, key label, uid, nickname,
// realm, masked access token and expiry. There is nothing to paste back and no
// import step; the connected accounts are listed underneath so the result of
// the sign-in is visible on the same screen.
//
// The flow itself is `useWorkBuddyLogin` from ./workbuddy-shared — the same
// machine the WorkBuddy page's dialog drives, so the two surfaces cannot drift.

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { workbuddyAccounts } from "@/api/client";
import {
  WorkBuddyAccountsTable,
  WorkBuddyLoginForm,
  useWorkBuddyLogin,
} from "@/pages/workbuddy-shared";
import { Card, CardHeader, EmptyState, ErrorText, PageHeader, Spinner } from "@/components/ui";

export function AccountLoginPage() {
  const [error, setError] = useState<string | null>(null);
  // The hook invalidates ["workbuddy-accounts"] and ["providers"] itself when
  // the account lands, so the table below refreshes on its own.
  const login = useWorkBuddyLogin();

  const accountsQ = useQuery({
    queryKey: ["workbuddy-accounts"],
    queryFn: workbuddyAccounts,
    refetchInterval: 15_000,
  });
  const accounts = accountsQ.data?.accounts ?? [];

  return (
    <div>
      <PageHeader
        title="Account login"
        subtitle="Sign in to a provider account — every returned detail is saved automatically"
      />
      {error && <div className="mb-3"><ErrorText>{error}</ErrorText></div>}

      <Card className="mb-4">
        <CardHeader
          title="WorkBuddy sign-in"
          subtitle="Browser login against the CN or global WorkBuddy API"
        />
        <div className="px-4 pb-4 pt-2">
          <WorkBuddyLoginForm state={login} />
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Connected accounts"
          subtitle="Every pool key wiwi holds for WorkBuddy"
        />
        <div className="px-4 pb-4 pt-2">
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
    </div>
  );
}
