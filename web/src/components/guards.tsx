// Route guards for the role-aware console split. `loading` is true while the
// initial /auth/me probe is in flight; rendering nothing avoids a flash of
// the login redirect before the session is known.

import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "@/api/auth";

export function RequireUser({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  if (user.role !== "admin") return <Navigate to="/app" replace />;
  return <>{children}</>;
}
