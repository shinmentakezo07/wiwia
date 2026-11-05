/** Recover a lazy route after a deployment removes its old content hash.
 * The new HTML document has the current asset manifest; refresh once, then
 * let the error boundary handle a second failure rather than looping.
 */
import { lazy } from "react";
import type { ComponentType, LazyExoticComponent } from "react";

export interface RecoveryStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface RecoveryOptions {
  reload?: () => void;
  storage?: RecoveryStorage;
  online?: boolean;
  now?: () => number;
}

const RELOAD_KEY = "wiwi.chunk-reload-at";
const RETRY_AFTER_MS = 60_000;

export function recoverChunkError(
  error: unknown,
  options: RecoveryOptions = {},
): boolean {
  const message = error instanceof Error ? error.message : String(error);
  if (!/dynamically imported module|importing a module script/i.test(message)) {
    return false;
  }
  if (options.online === false || navigator.onLine === false) return false;
  const storage = options.storage ?? window.sessionStorage;
  const reload = options.reload ?? (() => window.location.reload());
  const now = options.now ?? (() => Date.now());
  try {
    const lastReload = Number(storage.getItem(RELOAD_KEY) ?? 0);
    if (Number.isFinite(lastReload) && now() - lastReload < RETRY_AFTER_MS) {
      return false;
    }
    storage.setItem(RELOAD_KEY, String(now()));
    reload();
    return true;
  } catch {
    // Blocked storage must never cause an unbounded reload loop.
    return false;
  }
}

export function lazyRoute<T extends ComponentType<unknown>>(
  load: () => Promise<{ default: T }>,
): LazyExoticComponent<T> {
  return lazy(lazyWithReload(load));
}

export function lazyWithReload<T>(
  load: () => Promise<T>,
  options: RecoveryOptions = {},
): () => Promise<T> {
  return () => load().catch((error: unknown) => {
    if (recoverChunkError(error, options)) return new Promise<never>(() => {});
    throw error;
  });
}
