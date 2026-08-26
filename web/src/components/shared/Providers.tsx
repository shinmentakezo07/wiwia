// Providers — root provider wrapper. In the Next.js reference this wired
// PostHog, next-themes, React Query, a chat-support widget, TimeZoneProvider,
// and the App config. In this SPA port most of those are unavailable, so this
// keeps only the pieces that work here: the ReferralHandler and
// SignupMethodTracker (mounted at the root so every route gets them). React
// Query and the AuthProvider are already set up in main.tsx, so this does not
// re-provide them.

import { Suspense } from "react";
import type { ReactNode } from "react";
import { ReferralHandler } from "@/components/shared/ReferralHandler";
import { SignupMethodTracker } from "@/components/shared/SignupMethodTracker";

interface ProvidersProps {
  children: ReactNode;
}

export function Providers({ children }: ProvidersProps) {
  return (
    <>
      {children}
      <Suspense>
        <ReferralHandler />
      </Suspense>
      <Suspense>
        <SignupMethodTracker />
      </Suspense>
    </>
  );
}
