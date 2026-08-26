// Auth context: session-cookie auth (username/password) with master-key bearer
// back-compat. The session is a server-held HttpOnly cookie set by /auth/login
// and /auth/signup; /auth/me reports the current user. Master-key admins also
// keep a bearer token in localStorage (loginWithMaster → setToken) so the
// bearer-only /admin/stream SSE and any legacy /admin/* call continue to work.
//
// Playground key: a fresh virtual key is minted on every login/signup and
// stored in sessionStorage (survives refresh, clears on tab close). The
// Playground uses it as the bearer for /v1/chat/completions. When missing
// (new tab with a valid session cookie), the Playground mints a fresh one
// via /auth/playground-key instead of the old /admin/keys/generate path,
// which created an orphaned key on every page load.

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  clearToken,
  getMe,
  getToken,
  loginUser,
  loginMaster,
  logoutSession,
  mintPlaygroundKey,
  setToken,
  signupUser,
} from "./client";
import type { User } from "./types";

const PG_KEY_STORAGE = "wiwi.playground_key";

interface AuthCtx {
  user: User | null;
  loading: boolean;
  signup: (username: string, password: string) => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  loginWithMaster: (key: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  /** Mint a fresh playground key on demand (used by the Playground when
   * sessionStorage has no cached key). Returns the key or "" on failure. */
  ensurePlaygroundKey: () => Promise<string>;
}

const Ctx = createContext<AuthCtx>(null!);
export const useAuth = () => useContext(Ctx);

function storePlaygroundKey(key: string) {
  if (key) {
    try {
      sessionStorage.setItem(PG_KEY_STORAGE, key);
    } catch {
      /* sessionStorage may be unavailable (private mode) — caller falls back */
    }
  }
}

function loadPlaygroundKey(): string {
  try {
    return sessionStorage.getItem(PG_KEY_STORAGE) ?? "";
  } catch {
    return "";
  }
}

function clearPlaygroundKey() {
  try {
    sessionStorage.removeItem(PG_KEY_STORAGE);
  } catch {
    /* ignore */
  }
}

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
  }, [refresh]);

  const signup = useCallback(async (u: string, p: string) => {
    const { user, playground_key } = await signupUser({ username: u, password: p });
    setUser(user);
    storePlaygroundKey(playground_key ?? "");
  }, []);

  const login = useCallback(async (u: string, p: string) => {
    const { user, playground_key } = await loginUser({ username: u, password: p });
    setUser(user);
    storePlaygroundKey(playground_key ?? "");
  }, []);

  const loginWithMaster = useCallback(async (k: string) => {
    // back-compat: keep the master key for bearer-style calls (/admin/stream
    // SSE, which is bearer-only, and any legacy /admin/* fetch).
    setToken(k);
    const { user, playground_key } = await loginMaster({ master_key: k });
    setUser(user);
    storePlaygroundKey(playground_key ?? "");
  }, []);

  const logout = useCallback(async () => {
    try {
      await logoutSession();
    } catch {
      /* ignore network errors on logout */
    }
    clearToken();
    clearPlaygroundKey();
    setUser(null);
  }, []);

  const ensurePlaygroundKey = useCallback(async () => {
    const cached = loadPlaygroundKey();
    if (cached) return cached;
    // New tab / first visit with a valid session cookie — mint a fresh key.
    try {
      const { key } = await mintPlaygroundKey();
      storePlaygroundKey(key);
      return key;
    } catch {
      return "";
    }
  }, []);

  return (
    <Ctx.Provider value={{ user, loading, signup, login, loginWithMaster, logout, refresh, ensurePlaygroundKey }}>
      {children}
    </Ctx.Provider>
  );
}

// Re-export token helpers so legacy call sites (e.g. AdminStreamProvider, the
// Settings master-key reveal) keep working through this module if imported
// from here. Prefer importing them from "@/api/client" directly.
export { getToken, setToken, clearToken };
