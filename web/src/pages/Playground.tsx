// Playground — authenticated chat playground with chat history sidebar.
// Talks to /v1/playground/completions, which is /v1/chat/completions behind
// the logged-in session cookie: the server resolves (and rotates) the
// playground virtual key itself, so no key material is held in the browser.
// Enhanced model selector with provider info, availability, and
// deployment details. Full-page chat arena with SSE streaming (abortable),
// markdown-rendered replies, per-message actions, hero empty state, latency /
// throughput stats, and localStorage-backed conversation history.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertCircle,
  ArrowDown,
  ArrowUpRight,
  Bot,
  Check,
  ChevronDown,
  CircleDot,
  Clock,
  Copy,
  Eraser,
  Gauge,
  Layers3,
  MessageSquare,
  MessageSquarePlus,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Plug,
  RefreshCcw,
  Search,
  Send,
  Sparkles,
  Square,
  Terminal,
  Trash2,
  User,
  X,
} from "lucide-react";
import { ApiError, getModels, getPlaygroundMetrics } from "@/api/client";
import type { ModelGroup, PlaygroundMetrics } from "@/api/types";
import { Link } from "react-router-dom";
import { Spinner } from "@/components/ui";
import { Markdown } from "@/components/Markdown";
import { HERO_BEAMS_COMPACT, HeroBeamBackdrop } from "@/components/HeroBeamBackdrop";
import {
  heroSuggestionGroupNames,
  heroSuggestionGroups,
  sampleSuggestions,
  type HeroSuggestionGroup,
} from "@/lib/hero-suggestions";
import {
  type ChatMsg,
  type Conversation,
  clearAllChats,
  createChat,
  deleteChat,
  loadChats,
  relativeTime,
  renameChat,
  updateChat,
} from "@/lib/chat-store";

type Role = "user" | "assistant";
/** A turn as the Playground holds it. `reasoning` is the model's thinking
 *  trace, kept separate from `content` so it can be shown in its own
 *  collapsed block — some models stream reasoning for tens of seconds before
 *  the first visible token, and dropping it makes that phase look like a
 *  hang (the SSE reader used to ignore `reasoning_content` entirely). */
type Msg = { id: string; role: Role; content: string; reasoning?: string; failed?: boolean };

/** Date group labels for the sidebar chat list, newest first. */
const SIDEBAR_GROUPS = ["Today", "Yesterday", "Previous 7 days", "Older"] as const;

function chatDateGroup(updated: number): (typeof SIDEBAR_GROUPS)[number] {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const d = new Date(updated);
  const startOfDay = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const dayDiff = Math.floor((startOfToday - startOfDay) / 86_400_000);
  if (dayDiff <= 0) return "Today";
  if (dayDiff === 1) return "Yesterday";
  if (dayDiff <= 7) return "Previous 7 days";
  return "Older";
}

/** Coalesce localStorage writes to at most one per this many ms. */
const _PERSIST_DEBOUNCE_MS = 400;
const _METRICS_POLL_DELAY_MS = 100;
const _METRICS_POLL_ATTEMPTS = 30;

function uid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

function fmtTokens(n: number | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString();
}

function fmtMs(ms: number): string {
  if (ms <= 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function fmtTps(n: number): string {
  return n > 0 ? `${n.toFixed(1)} tok/s` : "—";
}

/** Poll the exact request-log row until the async log pump has accepted it. */
async function waitForMetrics(
  requestId: string,
  signal: AbortSignal,
): Promise<PlaygroundMetrics> {
  for (let attempt = 0; attempt < _METRICS_POLL_ATTEMPTS; attempt += 1) {
    try {
      return await getPlaygroundMetrics(requestId, signal);
    } catch (error) {
      const notReady =
        error instanceof ApiError && (error.status === 404 || error.status === 0);
      if (!notReady || signal.aborted || attempt === _METRICS_POLL_ATTEMPTS - 1) throw error;
      await new Promise((resolve, reject) => {
        const timer = window.setTimeout(resolve, _METRICS_POLL_DELAY_MS);
        signal.addEventListener(
          "abort",
          () => {
            window.clearTimeout(timer);
            reject(new DOMException("Aborted", "AbortError"));
          },
          { once: true },
        );
      });
    }
  }
  throw new Error("request metrics unavailable");
}

/** Shared SSE reader. Returns the accumulated visible text and honors an abort
 *  signal.
 *
 *  Two frame kinds used to be dropped on the floor:
 *
 *  - `reasoning_content` (thinking models). A model can stream reasoning for
 *    tens of seconds before its first content token, so ignoring it left the
 *    user staring at typing dots for a turn that was in fact progressing —
 *    the single biggest contributor to the Playground "taking lots of time".
 *  - `error` (the gateway's terminal StreamError frame, which is followed by
 *    connection close rather than `[DONE]`). Ignoring it turned a real
 *    upstream failure into a silent `"(empty response)"`.
 *
 *  Both are surfaced now: reasoning through `setReasoning`, errors by
 *  throwing so `runStream`'s catch sets the banner. */
async function streamSSE(
  resp: Response,
  assistantId: string,
  setMessages: React.Dispatch<React.SetStateAction<Msg[]>>,
  setReasoning: (text: string) => void,
): Promise<string> {
  const reader = resp.body?.getReader();
  if (!reader) throw new Error("no response body");
  const decoder = new TextDecoder();
  let buffer = "";
  let accumulated = "";
  let reasoning = "";
  try {
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
        if (!data || data === "[DONE]") continue;
        try {
          const parsed = JSON.parse(data) as {
            choices?: { delta?: { content?: string; reasoning_content?: string } }[];
            error?: { message?: string } | string;
          };
          // Terminal error frame: the stream ends right after it, so this is
          // the only signal the turn failed.
          if (parsed.error) {
            const msg =
              typeof parsed.error === "string"
                ? parsed.error
                : parsed.error.message ?? "upstream error";
            throw new Error(msg);
          }
          const delta = parsed.choices?.[0]?.delta;
          // Reasoning must be applied even when it arrives in the same frame
          // as content — the two fields are independent.
          if (delta?.reasoning_content) {
            reasoning += delta.reasoning_content;
            setReasoning(reasoning);
          }
          if (delta?.content) {
            accumulated += delta.content;
            const snapshot = accumulated;
            setMessages((prev) =>
              prev.map((m) => (m.id === assistantId ? { ...m, content: snapshot } : m)),
            );
          }
        } catch (e) {
          // A real frame-level failure (our own error throw, above) must
          // propagate; only malformed JSON is ignorable.
          if (e instanceof SyntaxError) continue;
          throw e;
        }
      }
    }
  } catch (e) {
    // Release the body on the error paths too: a thrown error frame stops
    // reading mid-stream, and an un-released reader keeps the connection (and
    // its upstream socket) alive until GC.
    void reader.cancel().catch(() => {});
    if (e instanceof DOMException && e.name === "AbortError") return accumulated;
    throw e;
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
  return msgs.map((m) => ({
    id: m.id,
    role: m.role,
    content: m.content,
    ...(m.reasoning ? { reasoning: m.reasoning } : {}),
    ...(m.failed ? { failed: true } : {}),
  }));
}

/** Stored conversation → live message state. The inverse of `toChatMsgs`; a
 *  single mapping keeps every load path (mount, chat switch, delete) from
 *  dropping fields the others keep. */
function toMsgs(msgs: ChatMsg[]): Msg[] {
  return msgs.map((m) => ({
    id: m.id,
    role: m.role,
    content: m.content,
    ...(m.reasoning ? { reasoning: m.reasoning } : {}),
    ...(m.failed ? { failed: true } : {}),
  }));
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
  // Highlighted option for arrow-key navigation (combobox pattern).
  const [active, setActive] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        // Return focus to the trigger so keyboard users keep their place.
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
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

  // Opening the menu (or changing the query) puts the highlight on the
  // currently selected model, or the first result.
  useEffect(() => {
    if (!open) return;
    const idx = filtered.findIndex((g) => g.name === value);
    setActive(idx >= 0 ? idx : 0);
  }, [open, search, filtered, value]);

  // Keep the highlighted option in view while arrowing through the list.
  useEffect(() => {
    if (!open) return;
    document
      .getElementById(`pg-model-opt-${active}`)
      ?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  const pickModel = (name: string) => {
    onChange(name);
    setOpen(false);
    setSearch("");
    triggerRef.current?.focus();
  };

  return (
    <div className="relative" ref={ref}>
      {/* Trigger button */}
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          // Full combobox keyboard contract: open + move with arrows, commit
          // with Enter, close with Escape. Without this the menu was
          // mouse-only no matter how many focus rings it had.
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            if (!open) {
              setOpen(true);
              return;
            }
            setActive((a) =>
              e.key === "ArrowDown"
                ? Math.min(a + 1, filtered.length - 1)
                : Math.max(a - 1, 0),
            );
          } else if (e.key === "Enter" && open) {
            e.preventDefault();
            if (filtered[active]) pickModel(filtered[active].name);
          }
        }}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls="pg-model-listbox"
        aria-label={`Choose model${selected ? `, currently ${selected.name}` : ""}`}
        className="pg-model-trigger group flex min-h-11 max-w-full items-center gap-2 rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] px-3 py-2 text-[13px] transition-[border-color,background-color,box-shadow] duration-200 hover:border-[var(--admin-border-hover)] hover:bg-white/[0.035] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50 disabled:opacity-50"
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
        <div className="pg-model-menu absolute left-0 top-[calc(100%+6px)] z-50 w-[min(360px,calc(100vw-2rem))] overflow-hidden rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface-elevated)] shadow-2xl shadow-black/50">
          {/* Search */}
          <div className="border-b border-[var(--admin-border)] p-2">
            <div className="flex items-center gap-2 rounded-lg bg-white/[0.03] px-2.5 py-1.5">
              <Search className="h-3.5 w-3.5 shrink-0 text-[var(--admin-text-dim)]" />
              <input
                autoFocus
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => {
                  // The search input holds focus while the menu is open, so
                  // the arrow/Enter contract lives here, not on the trigger.
                  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                    e.preventDefault();
                    setActive((a) =>
                      e.key === "ArrowDown"
                        ? Math.min(a + 1, filtered.length - 1)
                        : Math.max(a - 1, 0),
                    );
                  } else if (e.key === "Enter") {
                    e.preventDefault();
                    if (filtered[active]) pickModel(filtered[active].name);
                  }
                }}
                role="combobox"
                aria-expanded
                aria-haspopup="listbox"
                aria-controls="pg-model-listbox"
                aria-activedescendant={
                  open && filtered[active] ? `pg-model-opt-${active}` : undefined
                }
                aria-label="Search models or providers"
                placeholder="Search models or providers…"
                className="w-full bg-transparent text-[13px] text-[var(--admin-text)] outline-none placeholder:text-[var(--admin-text-dim)]"
              />
            </div>
          </div>

          {/* Model list */}
          <div
            id="pg-model-listbox"
            className="pg-scroll max-h-[360px] overflow-y-auto p-1.5"
            role="listbox"
            aria-label="Available models"
          >
            {filtered.length === 0 ? (
              <div className="py-8 text-center text-[13px] text-[var(--admin-text-dim)]">No models found</div>
            ) : (
              filtered.map((g, idx) => {
                const isSelected = g.name === value;
                const available = g.deployments.some((d) => d.available && d.cooldown_remaining_s === 0);
                return (
                  <button
                    key={g.name}
                    id={`pg-model-opt-${idx}`}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    // Keep DOM focus on the trigger; this only moves the
                    // visual highlight (aria-activedescendant pattern).
                    onMouseMove={() => setActive(idx)}
                    onClick={() => pickModel(g.name)}
                    className={`group/item flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors ${
                      idx === active
                        ? isSelected
                          ? "bg-blue-500/15"
                          : "bg-white/[0.06]"
                        : isSelected
                          ? "bg-blue-500/10"
                          : "hover:bg-white/[0.03]"
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
  onRename: (id: string, title: string) => void;
  onClearAll: () => void;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const { chats, activeId, onSelect, onNew, onDelete, onRename, onClearAll, collapsed, onToggle } = props;
  const [query, setQuery] = useState("");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [confirmingClear, setConfirmingClear] = useState(false);
  // Two-step confirm auto-resets so a stale armed state can't linger.
  const clearTimer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(clearTimer.current), []);

  // Below 768px the sidebar is not a flex column, it is an overlay floating
  // above the arena. It therefore needs its own dismiss affordances: Escape,
  // and a tap-outside scrim rendered below the panel.
  const isOverlay = collapsed && typeof window !== "undefined" && window.innerWidth < 768;
  useEffect(() => {
    if (!isOverlay) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onToggle();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isOverlay, onToggle]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return chats;
    return chats.filter(
      (c) =>
        (c.title || "New chat").toLowerCase().includes(q) ||
        c.messages.some((m) => m.content.toLowerCase().includes(q)),
    );
  }, [chats, query]);

  // In overlay mode the panel covers the arena, so a selection has to dismiss
  // it or the chosen conversation stays hidden underneath.
  const pickChat = (id: string) => {
    onSelect(id);
    if (isOverlay) onToggle();
  };

  // Group filtered chats by recency, preserving the newest-first order.
  const grouped = useMemo(() => {
    const map = new Map<(typeof SIDEBAR_GROUPS)[number], Conversation[]>();
    for (const c of filtered) {
      const g = chatDateGroup(c.updated);
      const bucket = map.get(g);
      if (bucket) bucket.push(c);
      else map.set(g, [c]);
    }
    return SIDEBAR_GROUPS.filter((g) => map.has(g)).map((g) => [g, map.get(g)!] as const);
  }, [filtered]);

  if (collapsed) {
    return (
      <aside className="pg-rail flex shrink-0 flex-col items-center gap-2 border-r border-[var(--admin-border)] bg-[var(--admin-surface)] px-2 py-3" aria-label="Conversation shortcuts">
        <button
          type="button"
          onClick={onToggle}
          className="pg-icon-button flex h-11 w-11 items-center justify-center rounded-lg text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.05] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
          aria-label="Expand sidebar"
          title="Expand sidebar"
        >
          <PanelLeftOpen size={18} />
        </button>
        <div className="my-1 h-px w-6 bg-[var(--admin-border)]" aria-hidden />
        <button
          type="button"
          onClick={onNew}
          className="pg-icon-button flex h-11 w-11 items-center justify-center rounded-lg text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.05] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
          aria-label="New chat"
          title="New chat"
        >
          <MessageSquarePlus size={18} />
        </button>
        <div className="mt-auto flex h-8 w-8 items-center justify-center rounded-full border border-emerald-400/20 bg-emerald-400/[0.07] text-emerald-300" title="Playground session active" aria-label="Playground session active">
          <CircleDot size={14} />
        </div>
      </aside>
    );
  }

  const commitRename = () => {
    if (renamingId) onRename(renamingId, renameDraft);
    setRenamingId(null);
  };

  return (
    <>
      {/* Tap-outside scrim. aria-hidden: it is a pointer affordance, not a control. */}
      {isOverlay && (
        <div
          className="pg-scrim fixed inset-0 z-20 bg-black/60 backdrop-blur-[2px] md:hidden"
          onClick={onToggle}
          aria-hidden
        />
      )}
      <aside
        className={
          isOverlay
            ? "pg-sidebar pg-sidebar-enter fixed inset-y-0 left-0 z-30 flex flex-col border-r border-[var(--admin-border)] bg-[var(--admin-surface)] shadow-2xl shadow-black/70 md:hidden"
            : "pg-sidebar hidden flex-col border-r border-[var(--admin-border)] bg-[var(--admin-surface)] md:flex"
        }
        aria-label="Conversation history"
      >
        {/* Header */}
        <div className="pg-sidebar-header flex items-center justify-between px-4 py-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Layers3 size={14} className="text-brand-300" aria-hidden />
              <span className="text-[12px] font-semibold uppercase tracking-wider text-[var(--admin-text)]">
                Workspace
              </span>
            </div>
            <span className="mt-1 block truncate text-[11px] text-[var(--admin-text-dim)]">Local conversation history</span>
          </div>
          <button
            type="button"
            onClick={onToggle}
            className="pg-icon-button flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.05] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
            aria-label="Collapse sidebar"
            title="Collapse sidebar"
          >
            <PanelLeftClose size={16} />
          </button>
        </div>

        {/* New chat button */}
        <div className="px-3 pb-3">
          <button
            type="button"
            onClick={onNew}
            className="pg-new-chat flex min-h-11 w-full items-center gap-2 rounded-lg border border-[var(--admin-border)] bg-white/[0.025] px-3 py-2 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:border-brand-400/30 hover:bg-brand-500/[0.08] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
          >
            <span className="flex h-7 w-7 items-center justify-center rounded-md bg-brand-500/10 text-brand-300">
              <MessageSquarePlus size={15} />
            </span>
            <span>New conversation</span>
          </button>
        </div>

        {/* Search */}
        <div className="px-3 pb-2">
          <div className="flex items-center gap-2 rounded-lg border border-[var(--admin-border)] bg-white/[0.02] px-2.5 py-1.5 transition-colors focus-within:border-[var(--admin-border-hover)] focus-within:ring-2 focus-within:ring-blue-400/20">
            <Search className="h-3.5 w-3.5 shrink-0 text-[var(--admin-text-dim)]" />
            <input
              type="search"
              aria-label="Search conversations"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search chats…"
              className="w-full bg-transparent text-[12px] text-[var(--admin-text)] outline-none placeholder:text-[var(--admin-text-dim)]"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
                className="shrink-0 text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)]"
                aria-label="Clear search"
              >
                <X size={12} />
              </button>
            )}
          </div>
        </div>

        {/* Chat list, grouped by recency */}
        <div className="pg-scroll min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {filtered.length === 0 ? (
            <div className="px-3 py-8 text-center text-[12px] text-[var(--admin-text-dim)]">
              {query ? "No chats match your search." : "No conversations yet."}
            </div>
          ) : (
            grouped.map(([group, items]) => (
              <div key={group} className="mb-2">
                <div className="px-2 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--admin-text-dim)]">
                  {group}
                </div>
                <div className="space-y-0.5">
                  {items.map((c) =>
                    renamingId === c.id ? (
                      <div key={c.id} className="rounded-lg border border-brand-500/30 bg-white/[0.03] px-2 py-1.5">
                        <input
                          autoFocus
                          value={renameDraft}
                          onChange={(e) => setRenameDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                              e.preventDefault();
                              commitRename();
                            } else if (e.key === "Escape") {
                              e.stopPropagation();
                              setRenamingId(null);
                            }
                          }}
                          onBlur={commitRename}
                          className="w-full bg-transparent text-[13px] text-[var(--admin-text)] outline-none"
                          aria-label="Chat name"
                        />
                      </div>
                    ) : (
                      <div
                        key={c.id}
                        data-active={c.id === activeId}
                        role="button"
                        tabIndex={0}
                        aria-current={c.id === activeId ? "page" : undefined}
                        onClick={() => pickChat(c.id)}
                        onKeyDown={(e) => {
                          if (e.target !== e.currentTarget) return;
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            pickChat(c.id);
                          }
                        }}
                        className="pg-chat-item group flex cursor-pointer items-start gap-2.5 rounded-lg border border-transparent px-2.5 py-2.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
                      >
                        <span className="pg-chat-item-icon mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[var(--admin-text-dim)]" aria-hidden>
                          <MessageSquare size={12} />
                        </span>
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
                        {/* Revealed on hover, keyboard focus, and any coarse
                            pointer — a hover-only reveal is invisible on touch
                            (AUDIT #210). The buttons carry the binding 44px
                            minimum; the negative vertical margin keeps that box
                            inside the row's own padding so the row does not
                            grow, and nothing overlaps the neighbouring rows. */}
                        <div className="-my-1.5 flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 pointer-coarse:opacity-100">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              setRenamingId(c.id);
                              setRenameDraft(c.title || "");
                            }}
                            className="flex h-11 w-11 items-center justify-center rounded text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
                            aria-label="Rename chat"
                            title="Rename chat"
                          >
                            <Pencil size={12} />
                          </button>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              onDelete(c.id);
                            }}
                            className="flex h-11 w-11 items-center justify-center rounded text-[var(--admin-text-dim)] transition-colors hover:bg-red-500/10 hover:text-red-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400/50"
                            aria-label="Delete chat"
                            title="Delete chat"
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                      </div>
                    ),
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        {/* Footer */}
        <div className="shrink-0 border-t border-[var(--admin-border)] px-3 py-2">
          <div className="flex items-center justify-between">
            <Link
              to="/console"
              className="flex min-h-11 items-center gap-2 text-[12px] text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]"
            >
              <Terminal size={12} />
              Dashboard
            </Link>
            {chats.length > 0 && !confirmingClear && (
              <button
                type="button"
                onClick={() => {
                  setConfirmingClear(true);
                  window.clearTimeout(clearTimer.current);
                  clearTimer.current = window.setTimeout(() => setConfirmingClear(false), 4000);
                }}
                className="flex min-h-11 items-center gap-1.5 rounded-md px-1.5 py-1 text-[11px] text-[var(--admin-text-dim)] transition-colors hover:text-red-400"
                aria-label="Clear all chats"
                title="Clear all chats"
              >
                <Eraser size={12} />
                Clear all
              </button>
            )}
            {confirmingClear && (
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => {
                    onClearAll();
                    setConfirmingClear(false);
                  }}
                  className="rounded-md border border-red-500/30 bg-red-500/10 px-1.5 py-0.5 text-[11px] text-red-400 transition-colors hover:bg-red-500/20"
                >
                  Confirm
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmingClear(false)}
                  className="rounded-md px-1 py-0.5 text-[11px] text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)]"
                >
                  <X size={12} />
                </button>
              </div>
            )}
          </div>
        </div>
      </aside>
    </>
  );
}

// ── Session status ─────────────────────────────────────────────────────────

type SessionStatusKind = "ready" | "live" | "loading" | "warning";

/** Rendered in the top bar; hides its text label below 640px. The dot + label
 *  are decorative (aria-hidden): the composer's placeholder and the send
 *  button's disabled state already convey the same state to assistive tech,
 *  and a third live region announcing every streaming transition is noise. */
function SessionStatus(props: {
  status: SessionStatusKind;
  label: string;
  compact?: boolean;
}) {
  return (
    <span
      className={`pg-status pg-status-${props.status} ${props.compact ? "pg-status-compact" : ""}`}
      aria-hidden
    >
      <span className="pg-status-dot" aria-hidden />
      <span className="pg-status-label">{props.label}</span>
    </span>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

export function PlaygroundPage() {
  const modelsQ = useQuery({ queryKey: ["models"], queryFn: getModels });

  const [model, setModel] = useState<string>("");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<PlaygroundMetrics | null>(null);
  const [metricsPending, setMetricsPending] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const metricsAbortRef = useRef<AbortController | null>(null);
  const activeRunRef = useRef<string | null>(null);

  // Abort any in-flight completion when the page unmounts. Without this the
  // fetch and SSE reader kept running against a component that was gone: the
  // upstream finished, the virtual key was charged for output nobody would
  // ever see, and setMessages fired on an unmounted component (AUDIT #256).
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      metricsAbortRef.current?.abort();
    };
  }, []);

  // ── Chat history state ───────────────────────────────────────────────────
  const [chats, setChats] = useState<Conversation[]>([]);
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(typeof window !== "undefined" && window.innerWidth < 768);
  // Unsent composer text, kept per chat so switching conversations doesn't
  // lose a half-written message. Cleared entries are fine to keep around —
  // they're tiny strings, and the ref dies with the component.
  const draftsRef = useRef<Record<string, string>>({});
  // The most recent failed request, kept so the error banner can offer a
  // one-click retry that replays the exact same conversation.
  //
  // Only the history is stored. `runStream` already appended the user message
  // to `messages` (and the failure path below deliberately leaves it there so
  // the user doesn't lose their text), so re-supplying it as `userText` on the
  // retry appended a SECOND copy — compounding on every retry and persisting
  // to localStorage. `history` already carries that one copy, which is what
  // goes upstream.
  const failedRef = useRef<Msg[] | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const focusComposer = useCallback(() => {
    // Streamed updates steal focus back constantly if we're eager here; only
    // focus when the composer isn't already focused elsewhere.
    if (document.activeElement?.tagName !== "TEXTAREA") textareaRef.current?.focus();
  }, []);

  // Load chat history from localStorage on mount
  useEffect(() => {
    const loaded = loadChats();
    setChats(loaded);
    if (loaded.length > 0) {
      setActiveChatId(loaded[0]!.id);
      setMessages(toMsgs(loaded[0]!.messages));
      setModel(loaded[0]!.model);
    } else {
      // Create initial empty chat
      const chat = createChat("");
      setChats([chat]);
      setActiveChatId(chat.id);
    }
  }, []);

  // Persist messages to localStorage, debounced.
  //
  // Streaming calls setMessages once per token, and each call re-ran a full
  // read → JSON.stringify → write → parse over every stored conversation on
  // the main thread. A long reply meant thousands of those cycles, which is
  // what made the page heavier the longer you used it. Coalesce to at most
  // one write per _PERSIST_DEBOUNCE_MS; a trailing flush covers unmount and
  // chat switches so a pending write is never dropped.
  const pendingSave = useRef<{ id: string; msgs: ChatMsg[]; model?: string } | null>(null);
  useEffect(() => {
    if (!activeChatId) return;
    const payload = {
      id: activeChatId,
      msgs: toChatMsgs(messages),
      model: model || undefined,
    };
    pendingSave.current = payload;
    const timer = window.setTimeout(() => {
      updateChat(payload.id, payload.msgs, payload.model);
      // Refresh chat list ordering (most recent first)
      setChats(loadChats());
      if (pendingSave.current?.id === payload.id) pendingSave.current = null;
    }, _PERSIST_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [messages, activeChatId, model]);

  // Flush a pending save when unmounting or switching chats. Reads from the
  // ref (not the closure) so it always writes current data.
  useEffect(() => {
    return () => {
      const p = pendingSave.current;
      if (p) {
        updateChat(p.id, p.msgs, p.model);
        pendingSave.current = null;
      }
    };
  }, [activeChatId]);

  // hero suggestion state
  const [activeGroup, setActiveGroup] = useState<HeroSuggestionGroup>("Create");
  const [heroSuggestions, setHeroSuggestions] = useState<Record<HeroSuggestionGroup, readonly string[]> | null>(null);
  useEffect(() => setHeroSuggestions(buildHeroSuggestions()), []);

  // scroll state
  const scrollRef = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);

  const groups: ModelGroup[] = modelsQ.data?.groups ?? [];
  const effectiveModel = model || groups[0]?.name || "";

  // ── Chat management ───────────────────────────────────────────────────

  const cancelActiveRun = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    metricsAbortRef.current?.abort();
    metricsAbortRef.current = null;
    activeRunRef.current = null;
    setBusy(false);
    setStreaming(false);
    setMetricsPending(false);
  }, []);

  function handleNewChat() {
    cancelActiveRun();
    const chat = createChat(effectiveModel);
    setChats((prev) => [chat, ...prev]);
    setActiveChatId(chat.id);
    setMessages([]);
    setErr(null);
    setMetrics(null);
    setMetricsPending(false);
    setDraft(draftsRef.current[chat.id] ?? "");
    void setTimeout(() => focusComposer(), 0);
  }

  function handleSelectChat(id: string) {
    if (id === activeChatId) return;
    // A stream is bound to the conversation it started in; switching chats
    // mid-stream would otherwise append tokens into the *previous* chat's
    // storage. Stop both the completion and its late metrics lookup.
    cancelActiveRun();
    failedRef.current = null;
    const chat = chats.find((c) => c.id === id);
    if (!chat) return;
    draftsRef.current[activeChatId ?? ""] = draft;
    setActiveChatId(id);
    setMessages(toMsgs(chat.messages));
    setModel(chat.model);
    setErr(null);
    setMetrics(null);
    setMetricsPending(false);
    setDraft(draftsRef.current[id] ?? "");
    void setTimeout(() => focusComposer(), 0);
  }

  function handleDeleteChat(id: string) {
    if (id === activeChatId) cancelActiveRun();
    delete draftsRef.current[id];
    const remaining = deleteChat(id);
    setChats(remaining);
    if (activeChatId === id) {
      if (remaining.length > 0) {
        const next = remaining[0]!;
        setActiveChatId(next.id);
        setMessages(toMsgs(next.messages));
        setModel(next.model);
        setMetrics(null);
        setMetricsPending(false);
        setDraft(draftsRef.current[next.id] ?? "");
      } else {
        // Create a fresh empty chat
        const chat = createChat(effectiveModel);
        setChats([chat]);
        setActiveChatId(chat.id);
        setMessages([]);
        setModel(effectiveModel);
        setMetrics(null);
        setMetricsPending(false);
        setDraft("");
      }
    }
  }

  function handleRenameChat(id: string, title: string) {
    const remaining = renameChat(id, title);
    if (remaining) setChats(remaining);
  }

  function handleClearAllChats() {
    cancelActiveRun();
    clearAllChats();
    draftsRef.current = {};
    const chat = createChat(effectiveModel);
    setChats([chat]);
    setActiveChatId(chat.id);
    setMessages([]);
    setModel(effectiveModel);
    setErr(null);
    setMetrics(null);
    setMetricsPending(false);
    setDraft("");
  }

  // ── streaming send ────────────────────────────────────────────────────────

  // ── streaming core (shared by send + regenerate) ─────────────────────────

  const runStream = useCallback(
    async (history: Msg[], userText: string | null) => {
      if (busy) return;
      const assistantId = uid();
      const assistantMsg: Msg = { id: assistantId, role: "assistant", content: "", reasoning: "" };
      const userMsg: Msg | null = userText != null ? { id: uid(), role: "user", content: userText } : null;
      setMessages((prev) => [...prev, ...(userMsg ? [userMsg] : []), assistantMsg]);
      setErr(null);
      setBusy(true);
      setStreaming(true);
      setMetrics(null);
      setMetricsPending(false);

      // A completed turn may still be waiting for its async log row. Do not
      // let a new turn or a chat switch inherit that old lookup.
      abortRef.current?.abort();
      metricsAbortRef.current?.abort();
      activeRunRef.current = assistantId;
      const controller = new AbortController();
      abortRef.current = controller;
      const finishGeneration = () => {
        if (activeRunRef.current !== assistantId) return;
        setBusy(false);
        setStreaming(false);
        if (abortRef.current === controller) abortRef.current = null;
      };

      try {
        // /v1/playground/completions authenticates with the session cookie
        // (same-origin fetch → credentials included by default). The server
        // resolves and rotates the playground virtual key itself, so there is
        // no bearer to manage and no stale-key 401 to retry around: a 401
        // here means the session itself is gone, which is terminal for this
        // turn and message-level recoverable via the error banner's Retry.
        const resp = await fetch("/v1/playground/completions", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
          },
          body: JSON.stringify({
            model: effectiveModel,
            messages: history.map((m) => ({ role: m.role, content: m.content })),
            stream: true,
          }),
          signal: controller.signal,
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

        const requestId = resp.headers.get("x-wiwi-request-id")?.trim() ?? "";
        // Reasoning is attached to the assistant message as it arrives so the
        // thinking phase is visible instead of an apparently frozen composer.
        const setReasoning = (text: string) => {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, reasoning: text } : m)),
          );
        };
        const accumulated = await streamSSE(resp, assistantId, setMessages, setReasoning);
        failedRef.current = null;

        // Generation is complete once the last SSE frame is read. The server's
        // request-log row is written asynchronously, so do not keep the
        // composer disabled while polling for that row.
        finishGeneration();
        if (requestId && !controller.signal.aborted && activeRunRef.current === assistantId) {
          const metricsController = new AbortController();
          metricsAbortRef.current = metricsController;
          setMetricsPending(true);
          void (async () => {
            try {
              const exactMetrics = await waitForMetrics(
                requestId,
                metricsController.signal,
              );
              if (
                !metricsController.signal.aborted &&
                activeRunRef.current === assistantId
              ) {
                setMetrics(exactMetrics);
              }
            } catch (metricsError) {
              if (
                !metricsController.signal.aborted &&
                activeRunRef.current === assistantId
              ) {
                console.warn("Playground request metrics unavailable", metricsError);
              }
            } finally {
              if (
                !metricsController.signal.aborted &&
                activeRunRef.current === assistantId
              ) {
                setMetricsPending(false);
              }
              if (metricsAbortRef.current === metricsController) {
                metricsAbortRef.current = null;
              }
            }
          })();
        }

        if (!accumulated) {
          // A model can legitimately end a turn having produced only a
          // reasoning trace (truncated at the token limit). Saying "(empty
          // response)" there would contradict the thinking block shown right
          // above it, so distinguish the two cases.
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, content: m.reasoning ? "(no answer — reasoning only)" : "(empty response)" }
                : m,
            ),
          );
        }
      } catch (e) {
        // An abort is the user's own stop button: keep whatever streamed.
        if (e instanceof DOMException && e.name === "AbortError") {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId && !m.content && !m.reasoning
                ? { ...m, content: "(stopped)" }
                : m,
            ),
          );
        } else {
          setErr(e instanceof Error ? e.message : "request failed");
          // The user message stays on screen (it is what failed, and the
          // retry replays it), so the retry must NOT re-supply it — that is
          // what duplicated it on every retry.
          failedRef.current = history;
          // Text or reasoning that already reached the screen is not thrown
          // away: an error after tens of seconds of streaming used to wipe the
          // whole bubble. Only an empty placeholder is removed (the retry
          // re-creates one) — a bubble with content stays and is marked.
          setMessages((prev) =>
            prev
              .filter((m) => m.id !== assistantId || !!m.content || !!m.reasoning)
              .map((m) => (m.id === assistantId ? { ...m, failed: true } : m)),
          );
        }
      } finally {
        finishGeneration();
        if (activeRunRef.current === assistantId && !metricsAbortRef.current) {
          setMetricsPending(false);
        }
      }
    },
    [busy, effectiveModel],
  );

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || busy) return;
      if (!effectiveModel) {
        setErr("No models available. Add a provider with a model group first.");
        return;
      }
      setDraft("");
      draftsRef.current[activeChatId ?? ""] = "";
      await runStream([...messages, { id: uid(), role: "user", content: trimmed }], trimmed);
    },
    [effectiveModel, busy, messages, runStream, activeChatId],
  );

  const stop = useCallback(() => {
    cancelActiveRun();
  }, [cancelActiveRun]);

  // Escape aborts an in-flight stream — same as the stop button.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // The rename input and model dropdown handle their own Escape; only
      // act when the event isn't consumed by an editable surface that isn't
      // the composer (which is allowed to stop the stream).
      const target = e.target as HTMLElement | null;
      const inComposer = target === textareaRef.current;
      const inOtherInput =
        target?.tagName === "INPUT" ||
        (target?.tagName === "TEXTAREA" && !inComposer) ||
        target?.isContentEditable;
      if (e.key === "Escape" && streaming && !inOtherInput) {
        e.preventDefault();
        stop();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [streaming, stop]);

  // One-click retry of the last failed request: clears the banner and
  // replays the exact same conversation state through runStream.
  //
  // `userText` is null: the failed attempt's user message is still in
  // `messages`, and `failed` is the history that already contains it. Passing
  // the text again would append a second copy on every retry.
  const retryFailed = useCallback(() => {
    const failed = failedRef.current;
    if (!failed || busy) return;
    failedRef.current = null;
    setErr(null);
    // A failed turn that streamed partial text leaves its bubble behind (it is
    // kept so the user can read what arrived). Retry appends a fresh
    // placeholder, so that stale bubble has to go first or each retry would add
    // another dead one beside the live answer.
    setMessages((prev) => prev.filter((m) => !m.failed));
    void runStream(failed, null);
  }, [busy, runStream]);

  // ── regenerate last ───────────────────────────────────────────────────────

  const regenerate = useCallback(() => {
    if (busy) return;
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUser) return;
    const idx = messages.findIndex((m) => m.id === lastUser.id);
    const upTo = messages.slice(0, idx + 1);
    setMessages(upTo); // drop the old answer; runStream appends the new placeholder
    void runStream(upTo, null);
  }, [busy, messages, runStream]);

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
    const el = scrollRef.current;
    if (!el) return;
    // An empty workbench is intentionally top-aligned. Without this guard the
    // initial empty render is treated as an "at bottom" conversation and the
    // long prompt launcher scrolls its own title out of view on phones.
    if (messages.length === 0) {
      el.scrollTop = 0;
      return;
    }
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
  const activeConversation = chats.find((chat) => chat.id === activeChatId);
  const activeChatTitle = activeConversation?.title || "New conversation";
  const sessionStatus: SessionStatusKind = streaming ? "live" : "ready";
  const sessionStatusLabel = streaming ? "Streaming" : "Ready";

  return (
    <div data-admin className="relative z-0 flex h-dvh flex-col overflow-hidden bg-[var(--admin-bg)] text-[var(--admin-text)]">
      {/* Shared site motion language: one restrained beam layer, no ambient glow. */}
      <div className="pg-atmosphere pointer-events-none fixed inset-0" aria-hidden>
        <HeroBeamBackdrop beams={HERO_BEAMS_COMPACT} className="pg-workbench-beams" />
      </div>

      {/* ══ Top bar ══ */}
      <header className="admin-topbar pg-topbar relative z-30 shrink-0">
        <div className="flex min-h-[60px] flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2 sm:px-5">
          <Link to="/" className="pg-brand flex min-w-0 items-center gap-2.5 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50">
            <span className="pg-brand-mark flex h-8 w-8 shrink-0 items-center justify-center rounded-[9px]">
              <img src="/wiwi-logo.png" alt="wiwi" className="h-7 w-7 rounded-[8px] object-cover ring-1 ring-white/[0.08] ring-inset" />
            </span>
            <span className="min-w-0">
              <span className="block truncate text-[14px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">wiwi</span>
              <span className="hidden font-mono text-[9px] font-semibold uppercase tracking-[0.18em] text-[var(--admin-text-dim)] min-[420px]:block">Playground</span>
            </span>
          </Link>

          {/* Model selector gets the full second row on small screens. */}
          <div className="order-3 w-full min-w-0 sm:order-none sm:mx-auto sm:w-auto sm:max-w-[420px] sm:flex-1">
            <ModelSelector
              groups={groups}
              value={effectiveModel}
              onChange={setModel}
            />
          </div>

          <div className="ml-auto flex min-w-0 flex-wrap items-center gap-1 sm:gap-2">
            <SessionStatus status={sessionStatus} label={sessionStatusLabel} compact />
            <Link
              to="/console"
              className="pg-dashboard-link flex min-h-11 items-center rounded-[10px] px-2.5 text-[12px] text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)] sm:px-3 sm:text-[13px]"
            >
              <Activity size={14} aria-hidden />
              <span className="ml-2 hidden sm:inline">Dashboard</span>
              <span className="ml-2 sm:hidden">Console</span>
            </Link>
          </div>
        </div>
        <div className="admin-topbar-border h-px" />
      </header>

      {/* ══ Body: sidebar + chat arena ══ */}
      <div className="pg-body relative z-10 flex min-h-0 flex-1">
        {/* Sidebar */}
        <ChatSidebar
          chats={chats}
          activeId={activeChatId}
          onSelect={handleSelectChat}
          onNew={handleNewChat}
          onDelete={handleDeleteChat}
          onRename={handleRenameChat}
          onClearAll={handleClearAllChats}
          collapsed={sidebarCollapsed}
          onToggle={() => setSidebarCollapsed((v) => !v)}
        />

        {/* Chat arena */}
        <main className="pg-arena relative flex min-h-0 flex-1 flex-col" aria-label="Model workbench">
          {/* One slim line: the model is already shown in the top bar and the
              hero card; this bar only carries the chat title. */}
          <div className="pg-context-bar shrink-0 px-4 pt-2.5 sm:px-6">
            <div className="mx-auto flex max-w-[820px] items-center justify-between gap-4">
              <div className="flex min-w-0 items-center gap-2">
                <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--admin-text-dim)]">Conversation</span>
                <span className="text-[var(--admin-text-dim)]/50" aria-hidden>/</span>
                <span className="truncate text-[13px] font-medium text-[var(--admin-text)]">{activeChatTitle}</span>
              </div>
              {effectiveModel && (
                <span
                  className="hidden max-w-[220px] truncate font-mono text-[10px] text-[var(--admin-text-dim)] sm:block"
                  title="Routed through"
                >
                  → {effectiveModel}
                </span>
              )}
            </div>
          </div>

          {err && (
            <div className="shrink-0 px-4 py-2 sm:px-6">
              <div className="mx-auto flex max-w-[820px] items-center gap-2 rounded-[10px] border border-red-500/15 bg-red-500/[0.05] px-3 py-2 text-[12px] text-red-400">
                <AlertCircle size={14} className="shrink-0" />
                <span className="min-w-0 flex-1 break-words">{err}</span>
                {failedRef.current && !busy && (
                  <button
                    type="button"
                    onClick={retryFailed}
                    className="shrink-0 rounded-md border border-red-500/25 px-2 py-0.5 text-[11px] font-medium transition-colors hover:bg-red-500/15"
                  >
                    Retry
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setErr(null)}
                  className="shrink-0 rounded p-0.5 text-red-400/60 transition-colors hover:text-red-400"
                  aria-label="Dismiss error"
                >
                  <X size={13} />
                </button>
              </div>
            </div>
          )}

          {/* Messages */}
          <div
            ref={scrollRef}
            onScroll={handleScroll}
            className="pg-scroll pg-message-scroll min-h-0 flex-1 overflow-y-auto"
            role="log"
            aria-live="polite"
          >
            {isEmpty ? (
              <HeroEmptyState
                activeGroup={activeGroup}
                onGroupChange={setActiveGroup}
                suggestions={heroSuggestions}
                onPick={(s) => void send(s)}
                model={effectiveModel}
              />
            ) : (
              <div className="pg-message-column mx-auto max-w-[820px] px-4 py-6 sm:px-6 sm:py-8">
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
              className="pg-scroll-btn absolute bottom-24 left-1/2 z-10 flex h-11 w-11 -translate-x-1/2 items-center justify-center rounded-full border border-[var(--admin-border)] bg-[var(--admin-surface-elevated)] text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
              aria-label="Scroll to bottom"
            >
              <ArrowDown size={16} />
            </button>
          )}

          {/* Response stats */}
          {(metrics || metricsPending) && (
            <div className="pg-stats-enter shrink-0 px-4 pb-1 sm:px-6">
              <div className="pg-metrics mx-auto flex max-w-[820px] items-center gap-3 px-3 py-2">
                <div className="pg-metrics-label hidden shrink-0 items-center gap-1.5 sm:flex">
                  <Activity size={12} />
                  <span>Response</span>
                </div>
                <div className="pg-metrics-items flex min-w-0 flex-1 flex-wrap items-center gap-1.5 font-mono text-[11px] text-[var(--admin-text-dim)]">
                  {metrics ? (
                    <>
                      <span className="pg-metric pg-metric-tone-blue" title="Server-measured time to first token">
                        <Clock size={11} aria-hidden />
                        {fmtMs(metrics.ttft_ms)} <span className="pg-metric-label">ttft</span>
                      </span>
                      <span className="pg-metric pg-metric-tone-violet" title="Server-measured generation speed">
                        <Gauge size={11} aria-hidden />
                        {fmtTps(metrics.tps)}
                      </span>
                      <span className="pg-metric pg-metric-tone-emerald" title="Gateway request latency">
                        <Clock size={11} aria-hidden />
                        {fmtMs(metrics.latency_ms)} <span className="pg-metric-label">latency</span>
                      </span>
                      <span className="pg-metric pg-metric-tone-blue" title="Provider-reported input tokens">
                        <span className="h-1.5 w-1.5 rounded-full bg-blue-300/80" aria-hidden />
                        {fmtTokens(metrics.prompt_tokens)} <span className="pg-metric-label">in</span>
                      </span>
                      <span className="pg-metric pg-metric-tone-violet" title="Provider-reported output tokens">
                        <span className="h-1.5 w-1.5 rounded-full bg-violet-300/80" aria-hidden />
                        {fmtTokens(metrics.completion_tokens)} <span className="pg-metric-label">out</span>
                      </span>
                      <span className="pg-metric pg-metric-tone-neutral" title="Total tokens">
                        <span className="h-1.5 w-1.5 rounded-full bg-white/35" aria-hidden />
                        {fmtTokens(metrics.total_tokens)} <span className="pg-metric-label">total</span>
                      </span>
                      {metrics.usage_estimated && (
                        <span className="pg-metric pg-metric-estimated" title="Token counts were estimated by wiwi">
                          estimated
                        </span>
                      )}
                    </>
                  ) : (
                    <span className="pg-metrics-loading" role="status" aria-live="polite">Loading server metrics…</span>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Composer */}
          <div className="pg-composer-region sticky bottom-0 z-10 shrink-0 px-4 pt-3 sm:px-6">
            <form onSubmit={onSubmit} className="mx-auto max-w-[820px]">
              <div className="relative">
                <div className="pg-composer relative flex items-end gap-2 rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-2 shadow-lg shadow-black/30">
                  <textarea
                    ref={textareaRef}
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={onKeyDown}
                    placeholder="Message the model…"
                    aria-label="Message the model"
                    aria-describedby="composer-hints"
                    disabled={busy}
                    rows={1}
                    className="pg-textarea flex-1 resize-none bg-transparent px-3 py-2 text-[14px] leading-relaxed text-[var(--admin-text)] outline-none placeholder:text-[var(--admin-text-dim)] disabled:opacity-50"
                  />
                  {streaming ? (
                    <>
                      <span className="pg-kbd mr-1 hidden shrink-0 self-center sm:inline-flex" title="Press Escape to stop">
                        Esc
                      </span>
                      <button
                        type="button"
                        onClick={stop}
                        className="pg-stop-button flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-red-500/25 bg-red-500/10 text-red-300 transition-[background-color,transform] duration-150 hover:bg-red-500/20 active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400/50"
                        aria-label="Stop generating"
                        title="Stop generating"
                      >
                        <Square size={14} fill="currentColor" />
                      </button>
                    </>
                  ) : (
                    <button
                      type="submit"
                      disabled={busy || !draft.trim()}
                      className="pg-send-button flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-white transition-[filter,transform] duration-150 hover:brightness-110 active:scale-95 disabled:opacity-40 disabled:grayscale focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-300/60"
                      aria-label="Send"
                    >
                      {busy ? <Spinner className="h-4 w-4" /> : <Send size={16} />}
                    </button>
                  )}
                </div>
              </div>
              <div id="composer-hints" className="pg-composer-hints mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-1 text-[11px] text-[var(--admin-text-dim)]">
                <div className="flex items-center gap-3">
                  <span className="inline-flex items-center gap-1.5">
                    <kbd className="pg-kbd">Enter</kbd> send
                  </span>
                  <span className="hidden items-center gap-1.5 sm:inline-flex">
                    <kbd className="pg-kbd">Shift</kbd>+<kbd className="pg-kbd">Enter</kbd> newline
                  </span>
                  {/* Live draft length makes an over-long prompt visible before
                      it is sent. */}
                  {draft.trim().length > 0 && (
                    <span className="inline-flex items-center gap-1 font-mono tabular-nums text-[var(--admin-text-muted)]">
                      {draft.trim().length.toLocaleString()} chars
                    </span>
                  )}
                </div>
                <span className="inline-flex items-center gap-1.5">
                  <Plug size={11} /> streamed live
                </span>
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
  model: string;
}) {
  const { activeGroup, onGroupChange, suggestions, onPick, model } = props;
  const visible = heroSuggestionGroupNames;
  const current = suggestions?.[activeGroup] ?? [];

  return (
    <div className="pg-hero-shell relative flex min-h-full items-center justify-center overflow-hidden px-4 py-6 sm:px-6 sm:py-10">
      <div className="animate-hero-enter relative w-full max-w-[760px] text-center">
        <div className="mb-3 flex justify-center sm:mb-5">
          <div className="pg-hero-badge relative flex h-12 w-12 items-center justify-center rounded-2xl border border-white/[0.08] shadow-xl shadow-brand-900/30 sm:h-14 sm:w-14">
            <Sparkles className="relative h-5 w-5 text-white sm:h-6 sm:w-6" />
          </div>
        </div>

        <h2 className="pg-hero-title text-[26px] font-semibold tracking-[-0.025em] sm:text-[30px]">
          Start a model session
        </h2>
        <p className="mx-auto mt-1.5 max-w-md text-[13px] leading-relaxed text-[var(--admin-text-muted)] sm:mt-2 sm:text-[14px]">
          Choose a prompt below or write directly into the workbench. Responses stream here in real time.
        </p>

        {model && (
          <div className="pg-model-card mx-auto mt-4 max-w-[440px] rounded-2xl px-4 py-3 sm:mt-6">
            <div className="flex items-center gap-3">
              <div className="pg-model-card-icon flex h-9 w-9 shrink-0 items-center justify-center rounded-xl">
                <Terminal size={17} />
              </div>
              <div className="min-w-0 flex-1 text-left">
                <div className="truncate text-[13px] font-medium text-[var(--admin-text)]">{model}</div>
                <div className="mt-0.5 text-[11px] text-[var(--admin-text-dim)]">Streaming · local session</div>
              </div>
            </div>
          </div>
        )}

        {model && (
          <>
            <div className="pg-hero-tabs pg-hero-tabs-scroll mt-5 inline-flex max-w-full items-center gap-1 overflow-x-auto rounded-xl p-1 sm:mt-7" role="tablist" aria-label="Prompt categories">
              {visible.map((g) => (
                <button
                  key={g}
                  type="button"
                  role="tab"
                  aria-selected={activeGroup === g}
                  onClick={() => onGroupChange(g)}
                  className={`min-h-11 shrink-0 rounded-lg px-3.5 text-[12px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50 ${
                    activeGroup === g
                      ? "bg-white/[0.09] text-[var(--admin-text)] shadow-sm"
                      : "text-[var(--admin-text-muted)] hover:text-[var(--admin-text)]"
                  }`}
                >
                  {g}
                </button>
              ))}
            </div>

            <div className="pg-suggestion-grid mt-3 grid gap-2 text-left sm:grid-cols-2" role="tabpanel" aria-label={`${activeGroup} prompt suggestions`}>
              {current.map((s, i) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => onPick(s)}
                  className="pg-sugg-enter pg-suggestion-card group flex min-h-[58px] items-start gap-3 rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] px-3.5 py-2.5 text-left text-[13px] leading-snug text-[var(--admin-text-muted)] transition-[border-color,background-color,color,transform,box-shadow] duration-200 hover:-translate-y-0.5 hover:border-brand-400/30 hover:bg-brand-500/[0.06] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
                  style={{ animationDelay: `${i * 0.04}s` }}
                >
                  <span className="pg-suggestion-icon flex h-8 w-8 shrink-0 items-center justify-center rounded-lg">
                    <Sparkles className="h-4 w-4" />
                  </span>
                  <span className="min-w-0 flex-1">{s}</span>
                  <ArrowUpRight className="h-4 w-4 shrink-0 text-[var(--admin-text-dim)] opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />
                </button>
              ))}
            </div>
            {/* Keyboard hints live on the composer, directly beside the keys
                they describe; repeating them here put two identical rows on
                screen at once. */}
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

  const isStreamingThis = isLast && streaming && !isUser;

  if (isUser) {
    return (
      <div className="pg-msg-enter pg-message pg-message-user group flex justify-end gap-3 py-2">
        <div className="flex max-w-[86%] flex-col items-end sm:max-w-[78%]">
          <div className="pg-user-bubble rounded-2xl rounded-br-[6px] px-4 py-2.5 text-[14px] leading-relaxed text-white">
            <p className="whitespace-pre-wrap break-words">{msg.content}</p>
          </div>
          {msg.content && (
            <div className="pg-message-actions mt-1 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 pointer-coarse:opacity-100">
              <ActionButton onClick={handleCopy} label={copied ? "Copied" : "Copy"}>
                {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
              </ActionButton>
            </div>
          )}
        </div>
        <div className="pg-avatar-user mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg">
          <User size={14} className="text-white" />
        </div>
      </div>
    );
  }

  return (
    <div className="pg-msg-enter pg-message pg-message-assistant group flex gap-3 py-3">
      <div className="pg-avatar-assistant mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--admin-border)] bg-[var(--admin-surface-elevated)]">
        <Bot size={15} className="text-brand-300" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="pg-message-heading mb-1.5 flex items-center gap-2">
          <span className="pg-message-role text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--admin-text-muted)]">Assistant</span>
          <span className="h-3 w-px bg-[var(--admin-border)]" aria-hidden />
          <span className="truncate font-mono text-[10px] text-[var(--admin-text-dim)]">
            {model}
          </span>
        </div>
        <div className="pg-response text-[14px] leading-relaxed text-[var(--admin-text)]">
          {/* Reasoning goes first: it is what the model produces first, and on
              a thinking model it is the only visible progress for many seconds
              (the typing dots alone read as a hung request). */}
          {msg.reasoning ? (
            <ReasoningBlock
              text={msg.reasoning}
              live={isStreamingThis && !msg.content}
              caret={isStreamingThis && !msg.content}
            />
          ) : null}
          {isStreamingThis && !msg.content ? (
            <TypingDots />
          ) : (
            <Markdown content={msg.content} caret={isStreamingThis && !!msg.content} />
          )}
          {msg.failed && (
            <p className="pg-msg-failed mt-2 flex items-center gap-1.5 text-[11px] text-amber-400">
              <AlertCircle size={12} aria-hidden />
              Response interrupted — the text above is what arrived.
            </p>
          )}
        </div>
        {!isStreamingThis && (msg.content || msg.reasoning) && (
          <div
            className={`pg-message-actions mt-1 flex items-center gap-1 transition-opacity ${
              isLast
                ? "opacity-100"
                : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 pointer-coarse:opacity-100"
            }`}
          >
            <ActionButton onClick={handleCopy} label={copied ? "Copied" : "Copy"}>
              {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
            </ActionButton>
            {isLast && (
              <ActionButton onClick={onRetry} label="Retry" disabled={busy}>
                <RefreshCcw size={13} />
              </ActionButton>
            )}
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
      className="flex min-h-11 items-center gap-1 rounded-md px-2 text-[11px] text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50 disabled:opacity-40"
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

/** Collapsible model reasoning trace.
 *
 *  Thinking models can spend tens of seconds in this phase before their first
 *  visible token, so it is shown live while it is the only sign of progress and
 *  collapses once the answer arrives. Native `<details>` gives keyboard and
 *  screen-reader semantics for free. */
function ReasoningBlock({ text, live, caret }: { text: string; live: boolean; caret?: boolean }) {
  // Open follows `live` — open while thinking, collapsed once the answer
  // arrives — unless the user works the disclosure themselves, after which
  // their choice wins and later renders leave it alone.
  const [open, setOpen] = useState(live);
  const userToggled = useRef(false);
  useEffect(() => {
    if (!userToggled.current) setOpen(live);
  }, [live]);
  return (
    <details
      className="pg-reasoning mb-2"
      open={open}
      data-live={live ? "true" : "false"}
      onToggle={(e) => {
        // `toggle` also fires for our own programmatic `open` changes, so only
        // a user-initiated flip may claim control.
        if (userToggled.current) setOpen(e.currentTarget.open);
      }}
    >
      <summary
        className="pg-reasoning-summary"
        onClick={() => {
          userToggled.current = true;
        }}
      >
        <span className="pg-reasoning-label">
          <Sparkles size={11} aria-hidden />
          {live ? "Thinking…" : "Reasoning"}
        </span>
      </summary>
      <div className="pg-reasoning-body">
        <p className="pg-reasoning-text">
          {text}
          {caret && <span className="pg-caret" aria-hidden />}
        </p>
      </div>
    </details>
  );
}
