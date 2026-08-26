// Playground — authenticated chat playground with chat history sidebar.
// Uses the playground key minted at login (stored in sessionStorage by the
// auth context) as the bearer for /v1/chat/completions. If no key is cached
// (new tab with a valid session), it mints a fresh one via /auth/playground-key.
// Enhanced model selector with provider info, availability, and
// deployment details. Full-page chat arena with SSE streaming, hero empty
// state, message actions, and localStorage-backed conversation history.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  Check,
  ChevronDown,
  Copy,
  MessageSquarePlus,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCcw,
  Search,
  Send,
  Sparkles,
  Square,
  Terminal,
  Trash2,
  Zap,
} from "lucide-react";
import { getModels } from "@/api/client";
import { useAuth } from "@/api/auth";
import type { ModelGroup } from "@/api/types";
import { Link } from "react-router-dom";
import {
  Badge,
  ErrorText,
  Spinner,
} from "@/components/ui";
import {
  heroSuggestionGroupNames,
  heroSuggestionGroups,
  sampleSuggestions,
  type HeroSuggestionGroup,
} from "@/lib/hero-suggestions";
import {
  type ChatMsg,
  type Conversation,
  createChat,
  deleteChat,
  loadChats,
  relativeTime,
  updateChat,
} from "@/lib/chat-store";

type Role = "user" | "assistant";
type Msg = { id: string; role: Role; content: string };

function uid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

function fmtTokens(n: number | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString();
}

/** Shared SSE reader. */
async function streamSSE(
  resp: Response,
  assistantId: string,
  setMessages: React.Dispatch<React.SetStateAction<Msg[]>>,
  setUsage: React.Dispatch<
    React.SetStateAction<{ prompt_tokens: number; completion_tokens: number; total_tokens: number } | null>
  >,
): Promise<string> {
  const reader = resp.body?.getReader();
  if (!reader) throw new Error("no response body");
  const decoder = new TextDecoder();
  let buffer = "";
  let accumulated = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      const trimmedLine = line.trim();
      if (!trimmedLine || !trimmedLine.startsWith("data:")) continue;
      const data = trimmedLine.slice(5).trim();
      if (!data) continue;
      try {
        const parsed = JSON.parse(data) as {
          choices?: { delta?: { content?: string } }[];
          usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
        };
        const delta = parsed.choices?.[0]?.delta?.content;
        if (delta) {
          accumulated += delta;
          const snapshot = accumulated;
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, content: snapshot } : m)),
          );
        }
        if (parsed.usage) setUsage(parsed.usage);
      } catch {
        // ignore malformed chunks
      }
    }
  }
  return accumulated;
}

function buildHeroSuggestions(): Record<HeroSuggestionGroup, readonly string[]> {
  return {
    Create: sampleSuggestions(heroSuggestionGroups.Create, 5),
    Explore: sampleSuggestions(heroSuggestionGroups.Explore, 5),
    Code: sampleSuggestions(heroSuggestionGroups.Code, 5),
  };
}

/** Convert Msg[] to ChatMsg[] for persistence. */
function toChatMsgs(msgs: Msg[]): ChatMsg[] {
  return msgs.map((m) => ({ id: m.id, role: m.role, content: m.content }));
}

// ── provider icon (simple text badge) ──────────────────────────────────────

function providerColor(provider: string): string {
  const p = provider.toLowerCase();
  if (p.includes("openai")) return "text-emerald-400 bg-emerald-500/10";
  if (p.includes("anthropic")) return "text-orange-400 bg-orange-500/10";
  if (p.includes("gemini") || p.includes("google")) return "text-blue-400 bg-blue-500/10";
  if (p.includes("xai") || p.includes("grok")) return "text-zinc-300 bg-zinc-500/10";
  if (p.includes("deepseek")) return "text-indigo-400 bg-indigo-500/10";
  if (p.includes("mistral")) return "text-amber-400 bg-amber-500/10";
  if (p.includes("groq")) return "text-rose-400 bg-rose-500/10";
  if (p.includes("fireworks")) return "text-yellow-400 bg-yellow-500/10";
  return "text-[var(--admin-text-muted)] bg-white/[0.04]";
}

function ProviderTag({ name }: { name: string }) {
  return (
    <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-medium ${providerColor(name)}`}>
      {name}
    </span>
  );
}

// ── Model selector dropdown ────────────────────────────────────────────────

function ModelSelector(props: {
  groups: ModelGroup[];
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  const { groups, value, onChange, disabled } = props;
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const selected = groups.find((g) => g.name === value);
  const filtered = useMemo(() => {
    if (!search.trim()) return groups;
    const q = search.toLowerCase();
    return groups.filter(
      (g) =>
        g.name.toLowerCase().includes(q) ||
        g.deployments.some((d) => d.provider.toLowerCase().includes(q) || d.model_id.toLowerCase().includes(q)),
    );
  }, [groups, search]);

  return (
    <div className="relative" ref={ref}>
      {/* Trigger button */}
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        className="group flex items-center gap-2 rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] px-3 py-2 text-[13px] transition-colors hover:border-[var(--admin-border-hover)] disabled:opacity-50"
      >
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-blue-500/10">
          <Terminal className="h-3.5 w-3.5 text-blue-400" />
        </div>
        <div className="min-w-0 text-left">
          <div className="truncate text-[13px] font-medium text-[var(--admin-text)]">
            {selected ? selected.name : value || "Select model…"}
          </div>
          {selected && (
            <div className="flex items-center gap-1 text-[10px] text-[var(--admin-text-dim)]">
              {selected.deployments.slice(0, 2).map((d, i) => (
                <span key={i}>{d.provider}{i < Math.min(selected.deployments.length, 2) - 1 ? " ·" : ""}</span>
              ))}
              {selected.deployments.length > 2 && <span>+{selected.deployments.length - 2}</span>}
            </div>
          )}
        </div>
        <ChevronDown className={`h-4 w-4 shrink-0 text-[var(--admin-text-dim)] transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {/* Dropdown panel */}
      {open && (
        <div className="absolute left-0 top-[calc(100%+6px)] z-50 w-[340px] overflow-hidden rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface-elevated)] shadow-2xl shadow-black/50">
          {/* Search */}
          <div className="border-b border-[var(--admin-border)] p-2">
            <div className="flex items-center gap-2 rounded-lg bg-white/[0.03] px-2.5 py-1.5">
              <Search className="h-3.5 w-3.5 shrink-0 text-[var(--admin-text-dim)]" />
              <input
                autoFocus
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search models or providers…"
                className="w-full bg-transparent text-[13px] text-[var(--admin-text)] outline-none placeholder:text-[var(--admin-text-dim)]"
              />
            </div>
          </div>

          {/* Model list */}
          <div className="max-h-[360px] overflow-y-auto p-1.5">
            {filtered.length === 0 ? (
              <div className="py-8 text-center text-[13px] text-[var(--admin-text-dim)]">No models found</div>
            ) : (
              filtered.map((g) => {
                const isSelected = g.name === value;
                const available = g.deployments.some((d) => d.available && d.cooldown_remaining_s === 0);
                return (
                  <button
                    key={g.name}
                    type="button"
                    onClick={() => {
                      onChange(g.name);
                      setOpen(false);
                      setSearch("");
                    }}
                    className={`group/item flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors ${
                      isSelected ? "bg-blue-500/10" : "hover:bg-white/[0.03]"
                    }`}
                  >
                    {/* Selection check */}
                    <div className="mt-0.5 w-4 shrink-0">
                      {isSelected && <Check className="h-4 w-4 text-blue-400" />}
                    </div>
                    <div className="min-w-0 flex-1">
                      {/* Model name + availability dot */}
                      <div className="flex items-center gap-2">
                        <span className={`truncate text-[13px] font-medium ${isSelected ? "text-blue-300" : "text-[var(--admin-text)]"}`}>
                          {g.name}
                        </span>
                        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${available ? "bg-emerald-400" : "bg-amber-400"}`} />
                      </div>
                      {/* Provider tags */}
                      <div className="mt-1 flex flex-wrap gap-1">
                        {g.deployments.map((d, i) => (
                          <ProviderTag key={i} name={d.provider} />
                        ))}
                      </div>
                      {/* Underlying model IDs */}
                      <div className="mt-1 truncate font-mono text-[10px] text-[var(--admin-text-dim)]">
                        {g.deployments.map((d) => d.model_id).join(" · ")}
                      </div>
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Sidebar ─────────────────────────────────────────────────────────────────

function ChatSidebar(props: {
  chats: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const { chats, activeId, onSelect, onNew, onDelete, collapsed, onToggle } = props;

  if (collapsed) {
    return (
      <div className="flex shrink-0 flex-col items-center gap-2 border-r border-[var(--admin-border)] bg-[var(--admin-surface)] py-3 px-2">
        <button
          type="button"
          onClick={onToggle}
          className="flex h-9 w-9 items-center justify-center rounded-lg text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)]"
          aria-label="Expand sidebar"
          title="Expand sidebar"
        >
          <PanelLeftOpen size={18} />
        </button>
        <button
          type="button"
          onClick={onNew}
          className="flex h-9 w-9 items-center justify-center rounded-lg text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)]"
          aria-label="New chat"
          title="New chat"
        >
          <MessageSquarePlus size={18} />
        </button>
      </div>
    );
  }

  return (
    <div className="pg-sidebar flex shrink-0 flex-col border-r border-[var(--admin-border)] bg-[var(--admin-surface)]">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3">
        <span className="text-[12px] font-semibold uppercase tracking-wider text-[var(--admin-text-dim)]">
          Chats
        </span>
        <button
          type="button"
          onClick={onToggle}
          className="flex h-7 w-7 items-center justify-center rounded-lg text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)]"
          aria-label="Collapse sidebar"
          title="Collapse sidebar"
        >
          <PanelLeftClose size={16} />
        </button>
      </div>

      {/* New chat button */}
      <div className="px-3 pb-2">
        <button
          type="button"
          onClick={onNew}
          className="flex w-full items-center gap-2 rounded-lg border border-[var(--admin-border)] bg-white/[0.02] px-3 py-2 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:border-[var(--admin-border-hover)] hover:bg-white/[0.04]"
        >
          <MessageSquarePlus size={15} className="text-blue-400" />
          New chat
        </button>
      </div>

      {/* Chat list */}
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {chats.length === 0 ? (
          <div className="px-3 py-8 text-center text-[12px] text-[var(--admin-text-dim)]">
            No conversations yet.
          </div>
        ) : (
          <div className="space-y-0.5">
            {chats.map((c) => (
              <div
                key={c.id}
                data-active={c.id === activeId}
                onClick={() => onSelect(c.id)}
                className="pg-chat-item group flex cursor-pointer items-start gap-2 rounded-lg border border-transparent px-2.5 py-2"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className={`truncate text-[13px] ${c.id === activeId ? "text-blue-300 font-medium" : "text-[var(--admin-text-muted)]"}`}>
                      {c.title || "New chat"}
                    </span>
                  </div>
                  <span className="text-[10px] text-[var(--admin-text-dim)]">
                    {relativeTime(c.updated)} · {c.messages.length} msgs
                  </span>
                </div>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(c.id);
                  }}
                  className="mt-0.5 shrink-0 rounded p-1 text-[var(--admin-text-dim)] opacity-0 transition-opacity hover:text-red-400 group-hover:opacity-100"
                  aria-label="Delete chat"
                  title="Delete chat"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="shrink-0 border-t border-[var(--admin-border)] px-3 py-2">
        <Link
          to="/app"
          className="flex items-center gap-2 text-[12px] text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]"
        >
          <Terminal size={12} />
          Dashboard
        </Link>
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

export function PlaygroundPage() {
  const qc = useQueryClient();
  const { ensurePlaygroundKey } = useAuth();
  const modelsQ = useQuery({ queryKey: ["models"], queryFn: getModels });

  const [bearer, setBearer] = useState<string>("");
  const [model, setModel] = useState<string>("");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [usage, setUsage] = useState<{ prompt_tokens: number; completion_tokens: number; total_tokens: number } | null>(null);
  const [keyReady, setKeyReady] = useState(false);

  // ── Chat history state ───────────────────────────────────────────────────
  const [chats, setChats] = useState<Conversation[]>([]);
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  // Load chat history from localStorage on mount
  useEffect(() => {
    const loaded = loadChats();
    setChats(loaded);
    if (loaded.length > 0) {
      setActiveChatId(loaded[0]!.id);
      setMessages(loaded[0]!.messages.map((m) => ({ id: m.id, role: m.role, content: m.content })));
      setModel(loaded[0]!.model);
    } else {
      // Create initial empty chat
      const chat = createChat("");
      setChats([chat]);
      setActiveChatId(chat.id);
    }
  }, []);

  // Persist messages to localStorage whenever they change
  useEffect(() => {
    if (!activeChatId) return;
    updateChat(activeChatId, toChatMsgs(messages), model || undefined);
    // Refresh chat list ordering (most recent first)
    setChats(loadChats());
  }, [messages, activeChatId, model]);

  // hero suggestion state
  const [activeGroup, setActiveGroup] = useState<HeroSuggestionGroup>("Create");
  const [heroSuggestions, setHeroSuggestions] = useState<Record<HeroSuggestionGroup, readonly string[]> | null>(null);
  useEffect(() => setHeroSuggestions(buildHeroSuggestions()), []);

  // scroll state
  const scrollRef = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);

  const groups: ModelGroup[] = modelsQ.data?.groups ?? [];
  const effectiveModel = model || groups[0]?.name || "";

  // ── Obtain the playground key (cached in sessionStorage by auth context) ──
  // On mount: use the cached key from login, or mint a fresh one via the
  // session cookie when sessionStorage is empty (new tab / first visit).
  useEffect(() => {
    let cancelled = false;
    if (bearer || keyReady) return;
    void (async () => {
      const key = await ensurePlaygroundKey();
      if (cancelled) return;
      setBearer(key);
      setKeyReady(!!key);
      if (key) void qc.invalidateQueries({ queryKey: ["keys"] });
      else setErr("Could not create a playground key. Please log in again.");
    })();
    return () => { cancelled = true; };
  }, [bearer, keyReady, ensurePlaygroundKey, qc]);

  // ── Chat management ───────────────────────────────────────────────────────

  function handleNewChat() {
    const chat = createChat(effectiveModel);
    setChats((prev) => [chat, ...prev]);
    setActiveChatId(chat.id);
    setMessages([]);
    setErr(null);
    setUsage(null);
    setDraft("");
  }

  function handleSelectChat(id: string) {
    const chat = chats.find((c) => c.id === id);
    if (!chat) return;
    setActiveChatId(id);
    setMessages(chat.messages.map((m) => ({ id: m.id, role: m.role, content: m.content })));
    setModel(chat.model);
    setErr(null);
    setUsage(null);
    setDraft("");
  }

  function handleDeleteChat(id: string) {
    const remaining = deleteChat(id);
    setChats(remaining);
    if (activeChatId === id) {
      if (remaining.length > 0) {
        const next = remaining[0]!;
        setActiveChatId(next.id);
        setMessages(next.messages.map((m) => ({ id: m.id, role: m.role, content: m.content })));
        setModel(next.model);
      } else {
        // Create a fresh empty chat
        const chat = createChat(effectiveModel);
        setChats([chat]);
        setActiveChatId(chat.id);
        setMessages([]);
        setModel(effectiveModel);
      }
    }
  }

  // ── streaming send ────────────────────────────────────────────────────────

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || busy) return;
      if (!bearer.trim()) {
        setErr("Waiting for playground key to be created…");
        return;
      }
      if (!effectiveModel) {
        setErr("No models available. Add a provider with a model group first.");
        return;
      }

      setErr(null);
      setBusy(true);
      setStreaming(true);
      setUsage(null);

      const userMsg: Msg = { id: uid(), role: "user", content: trimmed };
      const assistantId = uid();
      const assistantMsg: Msg = { id: assistantId, role: "assistant", content: "" };
      const outgoingMessages = [...messages, { role: "user" as Role, content: trimmed }];
      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setDraft("");

      try {
        const resp = await fetch("/v1/chat/completions", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${bearer.trim()}`,
            Accept: "text/event-stream",
          },
          body: JSON.stringify({
            model: effectiveModel,
            messages: outgoingMessages.map((m) => ({ role: m.role, content: m.content })),
            stream: true,
          }),
        });

        if (!resp.ok) {
          const body = await resp.json().catch(() => null);
          const msg =
            (body as { error?: { message?: string } | string } | null)?.error &&
            typeof (body as { error: { message?: string } }).error === "object"
              ? (body as { error: { message?: string } }).error.message
              : (body as { error?: string } | null)?.error ?? `HTTP ${resp.status}`;
          throw new Error(msg ?? `HTTP ${resp.status}`);
        }

        const accumulated = await streamSSE(resp, assistantId, setMessages, setUsage);

        if (!accumulated) {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, content: "(empty response)" } : m)),
          );
        }
      } catch (e) {
        setErr(e instanceof Error ? e.message : "request failed");
        setMessages((prev) => prev.filter((m) => m.id !== assistantId));
      } finally {
        setBusy(false);
        setStreaming(false);
      }
    },
    [bearer, effectiveModel, busy, messages],
  );

  // ── regenerate last ───────────────────────────────────────────────────────

  const regenerate = useCallback(() => {
    if (busy) return;
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUser) return;
    const idx = messages.findIndex((m) => m.id === lastUser.id);
    const upTo = messages.slice(0, idx + 1);
    setMessages(upTo);

    const assistantId = uid();
    setMessages((prev) => [...prev, { id: assistantId, role: "assistant", content: "" }]);
    setBusy(true);
    setStreaming(true);
    setErr(null);
    setUsage(null);

    void (async () => {
      try {
        const resp = await fetch("/v1/chat/completions", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${bearer.trim()}`,
            Accept: "text/event-stream",
          },
          body: JSON.stringify({
            model: effectiveModel,
            messages: upTo.map((m) => ({ role: m.role, content: m.content })),
            stream: true,
          }),
        });
        if (!resp.ok) {
          const body = await resp.json().catch(() => null);
          throw new Error(
            (body as { error?: { message?: string } } | null)?.error?.message ?? `HTTP ${resp.status}`,
          );
        }
        const accumulated = await streamSSE(resp, assistantId, setMessages, setUsage);
        if (!accumulated) {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, content: "(empty response)" } : m)),
          );
        }
      } catch (e) {
        setErr(e instanceof Error ? e.message : "request failed");
        setMessages((prev) => prev.filter((m) => m.id !== assistantId));
      } finally {
        setBusy(false);
        setStreaming(false);
      }
    })();
  }, [busy, messages, bearer, effectiveModel]);

  // ── scroll management ─────────────────────────────────────────────────────

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const isEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    setAtBottom(isEnd);
  }, []);

  useLayoutEffect(() => {
    if (atBottom) scrollToBottom();
  }, [messages, atBottom, scrollToBottom]);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void send(draft);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send(draft);
    }
  };

  const isEmpty = messages.length === 0;
  const keyLoading = !keyReady && !bearer;

  return (
    <div data-admin className="relative z-0 flex h-dvh flex-col overflow-hidden bg-[var(--admin-bg)] text-[var(--admin-text)]">
      {/* Ambient background */}
      <div className="pointer-events-none fixed inset-0" style={{ zIndex: 0 }}>
        <div
          className="absolute inset-0 opacity-[0.015]"
          style={{
            backgroundImage:
              "linear-gradient(rgba(255,255,255,0.08) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.08) 1px, transparent 1px)",
            backgroundSize: "64px 64px",
          }}
        />
      </div>

      {/* ══ Top bar ══ */}
      <header className="admin-topbar relative z-30 shrink-0">
        <div className="flex h-[52px] items-center gap-4 px-4">
          <Link to="/" className="flex items-center gap-2.5">
            <img src="/wiwi-logo.png" alt="wiwi" className="h-7 w-7 shrink-0 rounded-[8px] object-cover ring-1 ring-white/[0.06] ring-inset" />
            <span className="text-[14px] font-semibold text-[var(--admin-text)]">wiwi</span>
            <span className="font-mono text-[9px] font-semibold uppercase tracking-[0.18em] text-[var(--admin-text-dim)]">Playground</span>
          </Link>

          {/* Enhanced model selector — centered in the header */}
          <div className="mx-auto">
            <ModelSelector
              groups={groups}
              value={effectiveModel}
              onChange={setModel}
              disabled={keyLoading}
            />
          </div>

          <div className="flex items-center gap-1">
            {streaming && (
              <span className="admin-badge admin-badge-green mr-2">
                <span className="hero-live-dot text-emerald-400" />
                live
              </span>
            )}
            {keyLoading && (
              <span className="mr-2 flex items-center gap-1.5 text-[12px] text-[var(--admin-text-dim)]">
                <Spinner className="h-3.5 w-3.5" /> creating key…
              </span>
            )}
            <Link
              to="/app"
              className="flex items-center rounded-[10px] px-3 py-1.5 text-[13px] text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]"
            >
              Dashboard
            </Link>
          </div>
        </div>
        <div className="admin-topbar-border h-px" />
      </header>

      {/* ══ Body: sidebar + chat arena ══ */}
      <div className="relative z-10 flex min-h-0 flex-1">
        {/* Sidebar */}
        <ChatSidebar
          chats={chats}
          activeId={activeChatId}
          onSelect={handleSelectChat}
          onNew={handleNewChat}
          onDelete={handleDeleteChat}
          collapsed={sidebarCollapsed}
          onToggle={() => setSidebarCollapsed((v) => !v)}
        />

        {/* Chat arena */}
        <main className="relative flex min-h-0 flex-1 flex-col">
          {err && (
            <div className="shrink-0 px-6 py-2">
              <div className="mx-auto max-w-[760px]">
                <ErrorText>{err}</ErrorText>
              </div>
            </div>
          )}

          {/* Messages */}
          <div
            ref={scrollRef}
            onScroll={handleScroll}
            className="min-h-0 flex-1 overflow-y-auto"
            role="log"
            aria-live="polite"
          >
            {isEmpty ? (
              <HeroEmptyState
                activeGroup={activeGroup}
                onGroupChange={setActiveGroup}
                suggestions={heroSuggestions}
                onPick={(s) => void send(s)}
                keyReady={!!bearer}
              />
            ) : (
              <div className="mx-auto max-w-[760px] px-6 py-6">
                {messages.map((m, i) => (
                  <MessageBubble
                    key={m.id}
                    msg={m}
                    isLast={i === messages.length - 1}
                    streaming={streaming}
                    model={effectiveModel}
                    onCopy={() => void navigator.clipboard.writeText(m.content)}
                    onRetry={regenerate}
                    busy={busy}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Scroll-to-bottom */}
          {!isEmpty && !atBottom && (
            <button
              type="button"
              onClick={scrollToBottom}
              className="absolute bottom-24 left-1/2 z-10 flex h-9 w-9 -translate-x-1/2 items-center justify-center rounded-full border border-[var(--admin-border)] bg-[var(--admin-surface-elevated)] text-[var(--admin-text-muted)] shadow-lg transition-colors hover:text-[var(--admin-text)]"
              aria-label="Scroll to bottom"
            >
              <ArrowDown size={16} />
            </button>
          )}

          {/* Usage badges */}
          {usage && (
            <div className="shrink-0 px-6 pb-1">
              <div className="mx-auto flex max-w-[760px] flex-wrap gap-2">
                <Badge tone="blue">in {fmtTokens(usage.prompt_tokens)}</Badge>
                <Badge tone="violet">out {fmtTokens(usage.completion_tokens)}</Badge>
                <Badge tone="gray">total {fmtTokens(usage.total_tokens)}</Badge>
              </div>
            </div>
          )}

          {/* Composer */}
          <div className="shrink-0 bg-gradient-to-t from-[var(--admin-bg)] via-[var(--admin-bg)] to-transparent pt-2">
            <form onSubmit={onSubmit} className="mx-auto max-w-[760px] px-6 pb-4">
              <div className="relative">
                <div className="pointer-events-none absolute -inset-0.5 rounded-2xl bg-gradient-to-r from-blue-500/8 via-transparent to-fuchsia-500/8 opacity-0 transition-opacity focus-within:opacity-100" aria-hidden />
                <div className="relative flex items-end gap-2 rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-2 shadow-lg shadow-black/30 transition-colors focus-within:border-[var(--admin-border-hover)]">
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={onKeyDown}
                    placeholder={keyLoading ? "Preparing your playground key…" : "Message the model…"}
                    disabled={busy || keyLoading}
                    rows={1}
                    className="pg-textarea flex-1 resize-none bg-transparent px-3 py-2 text-[14px] leading-relaxed text-[var(--admin-text)] outline-none placeholder:text-[var(--admin-text-dim)] disabled:opacity-50"
                  />
                  <button
                    type="submit"
                    disabled={busy || keyLoading || !draft.trim()}
                    className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-b from-brand-400 to-brand-700 text-white shadow-lg shadow-brand-600/20 transition-[filter,transform] duration-150 hover:brightness-110 active:scale-95 disabled:opacity-40 disabled:grayscale"
                    aria-label={streaming ? "Stop" : "Send"}
                  >
                    {streaming ? <Square size={16} /> : busy ? <Spinner className="h-4 w-4" /> : <Send size={16} />}
                  </button>
                </div>
              </div>
            </form>
          </div>
        </main>
      </div>
    </div>
  );
}

// ── Hero empty state ───────────────────────────────────────────────────────

function HeroEmptyState(props: {
  activeGroup: HeroSuggestionGroup;
  onGroupChange: (g: HeroSuggestionGroup) => void;
  suggestions: Record<HeroSuggestionGroup, readonly string[]> | null;
  onPick: (s: string) => void;
  keyReady: boolean;
}) {
  const { activeGroup, onGroupChange, suggestions, onPick, keyReady } = props;
  const visible = heroSuggestionGroupNames;
  const current = suggestions?.[activeGroup] ?? [];

  return (
    <div className="relative flex min-h-full items-center justify-center overflow-hidden">
      <div className="hero-orb-1 pointer-events-none absolute -left-16 top-0 h-[340px] w-[340px] rounded-full" style={{ background: "radial-gradient(circle, rgba(59,130,246,0.08) 0%, transparent 60%)" }} aria-hidden />
      <div className="hero-orb-2 pointer-events-none absolute -bottom-20 -right-16 h-[300px] w-[300px] rounded-full" style={{ background: "radial-gradient(circle, rgba(168,85,247,0.06) 0%, transparent 60%)" }} aria-hidden />

      <div className="animate-hero-enter relative w-full max-w-[640px] px-6 text-center">
        <div className="mb-5 flex justify-center">
          <div className="relative flex h-14 w-14 items-center justify-center rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] shadow-lg">
            <div className="absolute inset-0 rounded-2xl bg-blue-500/5 blur-xl" aria-hidden />
            {keyReady ? (
              <Terminal className="relative h-6 w-6 text-blue-400" />
            ) : (
              <Spinner className="relative h-5 w-5" />
            )}
          </div>
        </div>

        <h2 className="text-2xl font-semibold tracking-tight text-[var(--admin-text)]">
          {keyReady ? "How can I help you?" : "Preparing your playground…"}
        </h2>
        <p className="mx-auto mt-1.5 max-w-md text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          {keyReady ? "Pick a suggestion or type your own message below." : "Creating a virtual key for your session."}
        </p>

        {keyReady && (
          <>
            {/* Group tabs */}
            <div className="mt-6 mb-4 flex justify-center gap-2">
              {visible.map((g) => (
                <button
                  key={g}
                  type="button"
                  onClick={() => onGroupChange(g)}
                  className={`rounded-full px-4 py-1.5 text-[13px] font-medium transition-colors ${
                    activeGroup === g
                      ? "bg-blue-500/15 text-blue-300"
                      : "border border-[var(--admin-border)] bg-[var(--admin-surface)] text-[var(--admin-text-muted)] hover:text-[var(--admin-text)]"
                  }`}
                >
                  {g}
                </button>
              ))}
            </div>

            {/* Suggestion cards */}
            <div className="space-y-2">
              {current.map((s, i) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => onPick(s)}
                  className="pg-sugg-enter group w-full rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] px-4 py-3 text-left text-[14px] text-[var(--admin-text-muted)] transition-all hover:border-[var(--admin-border-hover)] hover:bg-white/[0.02] hover:text-[var(--admin-text)] hover:shadow-lg hover:shadow-black/20"
                  style={{ animationDelay: `${i * 0.04}s` }}
                >
                  <div className="flex items-center gap-2.5">
                    <Sparkles className="h-3.5 w-3.5 shrink-0 text-[var(--admin-text-dim)] transition-colors group-hover:text-blue-400" />
                    <span>{s}</span>
                  </div>
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── Message bubble ─────────────────────────────────────────────────────────

function MessageBubble(props: {
  msg: Msg;
  isLast: boolean;
  streaming: boolean;
  model: string;
  onCopy: () => void;
  onRetry: () => void;
  busy: boolean;
}) {
  const { msg, isLast, streaming, model, onCopy, onRetry, busy } = props;
  const isUser = msg.role === "user";
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    onCopy();
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const isStreamingThis = isLast && streaming && !isUser && !msg.content;

  return (
    <div className={`pg-msg-enter group flex ${isUser ? "justify-end" : "justify-start"} py-1.5`}>
      <div className={`flex max-w-[80%] flex-col ${isUser ? "items-end" : "items-start"}`}>
        {!isUser && (
          <div className="mb-1 flex items-center gap-1.5">
            <Zap size={10} className="text-blue-400" />
            <span className="font-mono text-[10px] uppercase tracking-wider text-[var(--admin-text-dim)]">
              {model}
            </span>
          </div>
        )}
        <div
          className={`rounded-2xl px-4 py-2.5 text-[14px] leading-relaxed ${
            isUser
              ? "bg-blue-500/15 text-[var(--admin-text)]"
              : "border border-[var(--admin-border)] bg-white/[0.03] text-[var(--admin-text)]"
          }`}
        >
          {isStreamingThis ? (
            <TypingDots />
          ) : (
            <p className={`whitespace-pre-wrap break-words ${isLast && streaming && !isUser ? "pg-caret" : ""}`}>
              {msg.content || (isLast && !isUser && busy ? "…" : "")}
            </p>
          )}
        </div>
        {!isUser && isLast && !streaming && msg.content && (
          <div className="mt-1 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
            <ActionButton onClick={handleCopy} label={copied ? "Copied" : "Copy"}>
              {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
            </ActionButton>
            <ActionButton onClick={onRetry} label="Retry" disabled={busy}>
              <RefreshCcw size={13} />
            </ActionButton>
          </div>
        )}
      </div>
    </div>
  );
}

function ActionButton(props: { onClick: () => void; label: string; disabled?: boolean; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      disabled={props.disabled}
      className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)] disabled:opacity-40"
    >
      {props.children}
      {props.label}
    </button>
  );
}

function TypingDots() {
  return (
    <div className="flex items-center gap-1.5 py-1">
      <span className="pg-typing-dot" />
      <span className="pg-typing-dot" />
      <span className="pg-typing-dot" />
    </div>
  );
}
