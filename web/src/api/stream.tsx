// Single admin SSE connection shared app-wide; pages subscribe per event type.
// The connection lives while the user is authenticated.

import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { WiwiStream } from "./sse";
import { getToken } from "./client";

type Handler = (data: unknown, id: number) => void;

interface StreamCtx {
  connected: boolean;
  subscribe: (event: string, handler: Handler) => () => void;
}

const Ctx = createContext<StreamCtx>({
  connected: false,
  subscribe: () => () => undefined,
});

export function AdminStreamProvider({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const handlers = useRef(new Map<string, Set<Handler>>());

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    const stream = new WiwiStream("/admin/stream", token, {
      "log.created": (data, id) =>
        handlers.current.get("log.created")?.forEach((h) => h(data, id)),
      "proxy.log": (data, id) =>
        handlers.current.get("proxy.log")?.forEach((h) => h(data, id)),
    });
    stream.onStateChange = setConnected;
    stream.start();
    return () => stream.close();
  }, []);

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
