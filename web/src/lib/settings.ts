// Client-side console preferences, persisted to localStorage and applied to the
// document root so they take effect app-wide (not just on the Settings page).
//
// Implemented as a tiny external store with useSyncExternalStore so every
// component reading prefs re-renders on change, and DOM side-effects fire from
// a single authoritative `applyPrefsToDom` call.

import { useCallback, useEffect, useSyncExternalStore } from "react";

export interface ClientPrefs {
  /** Disable animations/transitions across the console. */
  reduceMotion: boolean;
  /** Table / list padding density. */
  density: "comfortable" | "compact";
  /** 24-hour clock in the topbar (true) vs 12-hour am/pm (false). */
  clock24h: boolean;
}

const KEY = "wiwi.prefs";

const DEFAULTS: ClientPrefs = {
  reduceMotion: false,
  density: "comfortable",
  clock24h: true,
};

function load(): ClientPrefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw) as Partial<ClientPrefs>;
    return {
      reduceMotion: parsed.reduceMotion ?? DEFAULTS.reduceMotion,
      density: parsed.density === "compact" ? "compact" : "comfortable",
      clock24h: parsed.clock24h ?? DEFAULTS.clock24h,
    };
  } catch {
    return DEFAULTS;
  }
}

/** Mutate <html> classes + CSS vars to reflect the given prefs. Idempotent. */
export function applyPrefsToDom(prefs: ClientPrefs): void {
  const root = document.documentElement;
  root.classList.toggle("reduce-motion", prefs.reduceMotion);
  root.classList.toggle("ui-compact", prefs.density === "compact");
}

// -- external store ----------------------------------------------------------

let snapshot: ClientPrefs = load();
const listeners = new Set<() => void>();

function emit(): void {
  for (const l of listeners) l();
}

function commit(next: ClientPrefs): void {
  snapshot = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // storage may be unavailable (private mode); prefs still apply in-session
  }
  applyPrefsToDom(next);
  emit();
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  // keep DOM in sync if prefs change in another tab
  const onStorage = (e: StorageEvent) => {
    if (e.key === KEY) {
      snapshot = load();
      applyPrefsToDom(snapshot);
      emit();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", onStorage);
  };
}

// Apply once on module load so prefs are honoured before first paint.
applyPrefsToDom(snapshot);

/** Read + mutate client preferences. Safe to call from any component. */
export function useClientPrefs() {
  const prefs = useSyncExternalStore(
    subscribe,
    () => snapshot,
    () => DEFAULTS,
  );
  // Re-apply on mount in case something stripped the classes.
  useEffect(() => {
    applyPrefsToDom(snapshot);
  }, []);

  const update = useCallback((patch: Partial<ClientPrefs>) => {
    commit({ ...snapshot, ...patch });
  }, []);

  const reset = useCallback(() => {
    commit(DEFAULTS);
  }, []);

  return { prefs, update, reset };
}
