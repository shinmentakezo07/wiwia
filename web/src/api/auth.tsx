// Auth context: session-cookie auth (username/password) with master-key bearer
// back-compat. The session is a server-held HttpOnly cookie set by /auth/login
// and /auth/signup; /auth/me reports the current user. Master-key admins also
// keep a bearer token in localStorage (loginWithMaster → setToken) so the
// bearer-only /admin/stream SSE and any legacy /admin/* call continue to work.
//
// Playground: the Playground posts to /v1/playground/completions, which
// authenticates with the session cookie and resolves the playground virtual
// key server-side. No key material lives in the browser anymore — the old
// sessionStorage key + 401 re-mint dance is gone (it existed only because the
// per-owner key cap kept expiring the cached bearer out from under the tab).

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  clearToken,
  getMe,
  getToken,
  loginUser,
  loginMaster,
  logoutSession,
  setToken,
  signupUser,
} from "./client";
import type { User } from "./types";

interface AuthCtx {
  user: User | null;
  loading: boolean;
  signup: (username: string, password: string) => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  loginWithMaster: (key: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const Ctx = createContext<AuthCtx>(null!);
export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const { user } = await getMe();
      setUser(user);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);  const signup = useCallback(async (u: string, p: string) => {
    const { user } = await signupUser({ username: u, password: p });
    setUser(user);
  }, []);

  const login = useCallback(async (u: string, p: string) => {
    const { user } = await loginUser({ username: u, password: p });
    setUser(user);
  }, []);

  const loginWithMaster = useCallback(async (k: string) => {
    // back-compat: keep the master key for bearer-style calls (/admin/stream
    // SSE, which is bearer-only, and any legacy /admin/* fetch). Validate
    // first — persist only on success, so a rejected key never reaches
    // localStorage or the Settings page.
    const { user } = await loginMaster({ master_key: k });
    setToken(k);
    setUser(user);
  }, []);

  const logout = useCallback(async () => {
    try {
      await logoutSession();
    } catch {
      /* ignore network errors on logout */
    }
    clearToken();
    setUser(null);
  }, []);

  return (
    <Ctx.Provider value={{ user, loading, signup, login, loginWithMaster, logout, refresh }}>
      {children}
    </Ctx.Provider>
  );
}

// Re-export token helpers so legacy call sites (e.g. AdminStreamProvider, the
// Settings master-key reveal) keep working through this module if imported
// from here. Prefer importing them from "@/api/client" directly.
export { getToken, setToken, clearToken };
