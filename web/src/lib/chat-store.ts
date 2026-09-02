// localStorage-backed chat history for the playground.
// Stores conversations as a list of { id, title, model, messages, created, updated }.
// No backend dependency — survives page refresh, isolated per browser.

export interface ChatMsg {
  id: string;
  role: "user" | "assistant";
  content: string;
}

export interface Conversation {
  id: string;
  title: string;
  model: string;
  messages: ChatMsg[];
  created: number;
  updated: number;
}

const KEY = "wiwi.playground.chats";
const MAX_CHATS = 100;

function uid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

function read(): Conversation[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw) as Conversation[];
    if (!Array.isArray(arr)) return [];
    return arr;
  } catch {
    return [];
  }
}

function write(chats: Conversation[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(chats.slice(0, MAX_CHATS)));
  } catch {
    // quota exceeded — silently drop oldest
    try {
      localStorage.setItem(KEY, JSON.stringify(chats.slice(0, MAX_CHATS / 2)));
    } catch {
      /* give up */
    }
  }
}

/** Load all conversations, newest first. */
export function loadChats(): Conversation[] {
  return read().sort((a, b) => b.updated - a.updated);
}

/** Create a new empty conversation. Returns the new conversation. */
export function createChat(model: string): Conversation {
  const now = Date.now();
  const chat: Conversation = {
    id: uid(),
    title: "New chat",
    model,
    messages: [],
    created: now,
    updated: now,
  };
  const chats = read();
  chats.unshift(chat);
  write(chats);
  return chat;
}

/** Update a conversation's messages (and title from first user message). */
export function updateChat(
  id: string,
  messages: ChatMsg[],
  model?: string,
): Conversation | null {
  const chats = read();
  const idx = chats.findIndex((c) => c.id === id);
  if (idx === -1) return null;
  const firstUser = messages.find((m) => m.role === "user");
  const title = firstUser
    ? firstUser.content.slice(0, 48) + (firstUser.content.length > 48 ? "…" : "")
    : chats[idx]!.title;
  const updated: Conversation = {
    ...chats[idx]!,
    messages,
    title,
    model: model ?? chats[idx]!.model,
    updated: Date.now(),
  };
  chats[idx] = updated;
  write(chats);
  return updated;
}

/** Delete a conversation by id. Returns the new chat list. */
export function deleteChat(id: string): Conversation[] {
  const chats = read().filter((c) => c.id !== id);
  write(chats);
  return chats.sort((a, b) => b.updated - a.updated);
}

/** Rename a conversation in place. Returns the new chat list, or null if the
 * id is unknown. An empty/whitespace title falls back to "New chat". */
export function renameChat(id: string, title: string): Conversation[] | null {
  const chats = read();
  const idx = chats.findIndex((c) => c.id === id);
  if (idx === -1) return null;
  const clean = title.trim();
  chats[idx] = {
    ...chats[idx]!,
    title: clean || "New chat",
    // Renaming is metadata-only: don't bump `updated` (which drives ordering
    // and the relative-time label in the sidebar).
    updated: chats[idx]!.updated,
  };
  write(chats);
  return chats.sort((a, b) => b.updated - a.updated);
}

/** Clear all conversations. */
export function clearAllChats(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

/** Relative time formatter for sidebar timestamps. */
export function relativeTime(ts: number): string {
  const diff = Date.now() - ts;
  if (diff < 60_000) return "now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h`;
  if (diff < 604_800_000) return `${Math.floor(diff / 86_400_000)}d`;
  return new Date(ts).toLocaleDateString([], { month: "short", day: "numeric" });
}
