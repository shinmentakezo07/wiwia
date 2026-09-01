// Single admin SSE connection shared app-wide; pages subscribe per event type.
// The connection lives while the user is authenticated.

import { createContext, useContext, useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { WiwiStream } from "./sse";
import { getToken, subscribeToken } from "./client";

type Handler = (data: unknown, id: number) => void;

interface StreamCtx {
  connected: boolean;
  subscribe: (event: string, handler: Handler) => () => void;
}

const Ctx = createContext<StreamCtx>({
  connected: false,
  subscribe: () => () => undefined,
});

// Reactive token snapshot: re-reads on any setToken/clearToken (same tab via
// subscribeToken, cross-tab via the storage event bridged inside subscribeToken),
// so the effect below re-runs and the SSE connection always carries the current
// credential — not the one that happened to exist at mount.
function useToken(): string {
  return useSyncExternalStore(subscribeToken, getToken, () => "");
}

export function AdminStreamProvider({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const handlers = useRef(new Map<string, Set<Handler>>());
  const token = useToken();

  useEffect(() => {
    if (!token) return;
    const stream = new WiwiStream("/admin/stream", token, {
      "log.created": (data, id) =>
        handlers.current.get("log.created")?.forEach((h) => h(data, id)),
      "proxy.log": (data, id) =>
        handlers.current.get("proxy.log")?.forEach((h) => h(data, id)),
    });
    stream.onStateChange = setConnected;
    stream.start();
    return () => {
      stream.close();
      setConnected(false);
    };
  }, [token]);

  const subscribe = (event: string, handler: Handler) => {
    let set = handlers.current.get(event);
    if (!set) {
      set = new Set();
      handlers.current.set(event, set);
    }
    set.add(handler);
    return () => {
      set?.delete(handler);
    };
  };

  return <Ctx.Provider value={{ connected, subscribe }}>{children}</Ctx.Provider>;
}

/** Subscribe to an admin SSE event for the lifetime of the calling component. */
export function useAdminStream(event: string, handler: Handler): boolean {
  const { subscribe, connected } = useContext(Ctx);
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    return subscribe(event, (data, id) => ref.current(data, id));
  }, [event, subscribe]);
  return connected;
}

/**
 * Live-invalidate React Query caches whenever a `log.created` SSE event arrives,
 * so overview/timeseries/logs data refreshes immediately instead of waiting for
 * the next polling interval. Invalidation is throttled to at most once per
 * `minGapMs` to avoid stampeding the server with a burst of refetches when many
 * requests finish in quick succession. Returns the SSE connection state so the
 * caller can render a live badge.
 */
export function useLiveInvalidation(
  queryKeys: string[],
  minGapMs = 1500,
): boolean {
  const qc = useQueryClient();
  const lastRef = useRef(0);
  const connected = useAdminStream("log.created", () => {
    const now = Date.now();
    if (now - lastRef.current < minGapMs) return;
    lastRef.current = now;
    for (const key of queryKeys) {
      qc.invalidateQueries({ queryKey: [key] });
    }
  });
  return connected;
}
