// Auth context: master key in localStorage, validated once via /admin/keys probe.

import { createContext, useCallback, useContext, useState } from "react";
import type { ReactNode } from "react";
import { api, clearToken, getToken, setToken } from "./client";

interface AuthCtx {
  authed: boolean;
  login: (key: string) => Promise<string | null>; // null = ok, string = error
  logout: () => void;
}

const Ctx = createContext<AuthCtx>({
  authed: false,
  login: async () => "not initialized",
  logout: () => undefined,
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState(() => getToken() !== "");

  const login = useCallback(async (key: string): Promise<string | null> => {
    setToken(key);
    try {
      await api<unknown>("/admin/keys");
      setAuthed(true);
      return null;
    } catch (e) {
      clearToken();
      return e instanceof Error ? e.message : "login failed";
    }
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setAuthed(false);
  }, []);

  return <Ctx.Provider value={{ authed, login, logout }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  return useContext(Ctx);
}
