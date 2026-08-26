// Shared UI elements ported from the Next.js reference's components/shared/
// directory: auth-link, model-card, model-search, quick-start-snippet, and
// usage-mode-selector. All self-contained, using react-router and the
// project's dark admin design system.

import { Check, Code, Copy, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "@/api/auth";

// ── AuthLink ────────────────────────────────────────────────────────────────
// A link that sends authenticated users to the dashboard and others to signup.

export function AuthLink(props: { href: string; children: React.ReactNode; className?: string }) {
  const { user, loading } = useAuth();
  const to = user && !loading ? "/app" : "/signup";
  return (
    <Link to={to} className={props.className}>
      {props.children}
    </Link>
  );
}

// ── ModelCard ───────────────────────────────────────────────────────────────
// Compact card showing a model name, provider/model slug, context size, and
// pricing, with a copy button and a "See more details" link.

export function ModelCard({ modelName, providers }: { modelName: string; providers: Array<{ providerId: string; externalId: string; contextSize?: number | null; inputPrice?: number | null; outputPrice?: number | null; requestPrice?: number | null; pricingTiers?: unknown[] }> }) {
  const [copiedText, setCopiedText] = useState<string | null>(null);

  const copyToClipboard = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedText(text);
      setTimeout(() => setCopiedText(null), 2000);
    } catch (err) {
      console.error("Failed to copy text:", err);
    }
  };

  if (!providers || providers.length === 0) {
    return (
      <div className="admin-card flex flex-col p-4">
        <h3 className="line-clamp-1 text-base font-semibold text-[var(--admin-text)]">{modelName}</h3>
        <p className="text-xs text-[var(--admin-text-muted)]">No providers available</p>
      </div>
    );
  }

  const provider = providers[0];
  const providerModelName = `${provider.providerId}/${modelName}`;

  const formatContext = (n?: number | null) => {
    if (n == null) return null;
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(0)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
    return String(n);
  };

  return (
    <div className="admin-card flex flex-col p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="line-clamp-1 text-base font-semibold text-[var(--admin-text)]">{modelName}</h3>
          <p className="truncate text-xs text-[var(--admin-text-muted)]">{provider.externalId}</p>
        </div>
      </div>
      <div className="mt-2 flex items-center justify-between gap-2">
        <div className="min-w-0 flex-1">
          <code className="break-all rounded bg-white/[0.04] px-2 py-1 font-mono text-xs text-[var(--admin-text-muted)]">
            {providerModelName}
          </code>
        </div>
        <button
          type="button"
          onClick={(e) => { e.preventDefault(); void copyToClipboard(providerModelName); }}
          title="Copy provider/model name"
          className="shrink-0 rounded p-1 text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)]"
        >
          {copiedText === providerModelName ? <Check className="h-3 w-3 text-green-400" /> : <Copy className="h-3 w-3" />}
        </button>
      </div>
      {provider.contextSize && (
        <p className="mt-2 text-xs text-[var(--admin-text-muted)]">
          Context: <span className="font-mono font-bold text-[var(--admin-text)]">{formatContext(provider.contextSize)}</span>
        </p>
      )}
      {(provider.inputPrice != null || provider.outputPrice != null || provider.requestPrice != null) && (
        <div className="mt-1 text-xs text-[var(--admin-text-muted)]">
          {provider.inputPrice != null && Number.isFinite(provider.inputPrice) && (
            <>
              <span className="font-mono font-bold text-[var(--admin-text)]">
                ${(provider.inputPrice * 1e6).toFixed(2)}
              </span>{" "}
              <span>in</span>
            </>
          )}
          {provider.outputPrice != null && Number.isFinite(provider.outputPrice) && (
            <>
              <span className="mx-2">/</span>
              <span className="font-mono font-bold text-[var(--admin-text)]">
                ${(provider.outputPrice * 1e6).toFixed(2)}
              </span>{" "}
              <span>out</span>
            </>
          )}
          {provider.requestPrice != null && Number.isFinite(provider.requestPrice) && provider.requestPrice !== 0 && (
            ` / $${(provider.requestPrice * 1000).toFixed(2)} per 1K req`
          )}
          {provider.pricingTiers && provider.pricingTiers.length > 1 && (
            <p className="mt-0.5 text-[10px] text-[var(--admin-text-dim)]">Tiered pricing available</p>
          )}
        </div>
      )}
      <div className="mt-auto pt-4">
        <Link
          to={`/models/${encodeURIComponent(modelName)}`}
          className="admin-btn admin-btn-ghost w-full justify-center"
        >
          See more details
        </Link>
      </div>
    </div>
  );
}

// ── ModelSearch ─────────────────────────────────────────────────────────────
// A command-palette-style model search. The reference fetched models from the
// API; this version accepts models/providers as props (or uses an empty
// fallback) to stay self-contained.

interface ApiModel {
  id: string;
  name: string | null;
  family: string;
  releasedAt?: string | null;
  createdAt?: string;
  aliases?: string[] | null;
  mappings: Array<{ providerId: string; deactivatedAt?: string | null; requestPrice?: string | null }>;
}

interface ApiProvider {
  id: string;
  name: string | null;
}

interface ModelSearchEntry {
  id: string;
  name: string;
  family: string;
  createdAt?: Date;
  searchText: string;
}

function normalizeForSearch(value: string) {
  return value.toLowerCase().replace(/[-_\s]+/g, "");
}

function formatMonthLabel(date?: Date) {
  if (!date) return "Unknown date";
  return date.toLocaleDateString(undefined, { year: "numeric", month: "long" });
}

export function ModelSearch({ models = [], providers = [] }: { models?: ApiModel[]; providers?: ApiProvider[] }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        const target = event.target as HTMLElement | null;
        const isTypingElement =
          target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
        if (!isTypingElement) {
          event.preventDefault();
          setOpen(true);
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = 0;
  }, [search]);

  const entries = useMemo<ModelSearchEntry[]>(() => {
    const now = new Date();
    const map = new Map<string, ModelSearchEntry>();
    for (const model of models) {
      if (model.id === "custom") continue;
      const createdAt = model.createdAt ? new Date(model.createdAt) : model.releasedAt ? new Date(model.releasedAt) : undefined;
      const activeMappings = model.mappings.filter((mapping) => {
        const isDeactivated = mapping.deactivatedAt && new Date(mapping.deactivatedAt).getTime() <= now.getTime();
        return !isDeactivated;
      });
      if (activeMappings.length === 0) continue;
      const key = String(model.id);
      if (map.has(key)) continue;
      const entryName = model.name ?? String(model.id);
      const providerNames = activeMappings.map(
        (mapping) => providers.find((p) => p.id === mapping.providerId)?.name ?? String(mapping.providerId),
      );
      map.set(key, {
        id: String(model.id),
        name: entryName,
        family: model.family,
        createdAt,
        searchText: normalizeForSearch([...providerNames, entryName, String(model.id), model.family ?? "", model.aliases?.join(" ") ?? ""].join(" ")),
      });
    }
    const list = Array.from(map.values());
    list.sort((a, b) => {
      const aTime = a.createdAt?.getTime() ?? 0;
      const bTime = b.createdAt?.getTime() ?? 0;
      if (bTime !== aTime) return bTime - aTime;
      return a.name.localeCompare(b.name);
    });
    return list;
  }, [models, providers]);

  const searchTokens = useMemo(() => search.toLowerCase().split(/[-_\s]+/).filter(Boolean), [search]);

  const filteredEntries = useMemo(() => {
    if (searchTokens.length === 0) return entries;
    return entries.filter((entry) => searchTokens.every((token) => entry.searchText.includes(token)));
  }, [entries, searchTokens]);

  const filteredProviders = useMemo(() => {
    if (searchTokens.length === 0) return [];
    return providers.filter((p) => {
      if (p.name === "wiwi") return false;
      const text = normalizeForSearch(`${p.name ?? p.id} ${p.id}`);
      return searchTokens.every((token) => text.includes(token));
    });
  }, [providers, searchTokens]);

  const groups = useMemo(() => {
    const byMonth = new Map<string, ModelSearchEntry[]>();
    for (const entry of filteredEntries) {
      const label = formatMonthLabel(entry.createdAt);
      if (!byMonth.has(label)) byMonth.set(label, []);
      byMonth.get(label)!.push(entry);
    }
    return Array.from(byMonth.entries());
  }, [filteredEntries]);

  return (
    <div className="relative inline-block w-full">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 rounded-full border border-[var(--admin-border)] bg-[var(--admin-surface)]/60 px-3 py-1.5 text-xs text-[var(--admin-text-muted)] shadow-sm transition-colors hover:border-[var(--admin-border-hover)]"
      >
        <Search className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate">Search models by provider, name, ID, or alias…</span>
        <span className="ml-auto hidden rounded border border-[var(--admin-border)] bg-white/[0.03] px-1.5 py-0.5 text-[10px] font-medium text-[var(--admin-text-muted)] sm:inline-flex">
          ⌘K
        </span>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => { setOpen(false); setSearch(""); }} />
          <div className="absolute left-0 z-40 mt-1 w-[min(480px,90vw)] overflow-hidden rounded-lg border border-[var(--admin-border)] bg-[var(--admin-surface-elevated)] shadow-xl">
            <div className="border-b border-[var(--admin-border)] px-3 py-2">
              <input
                autoFocus
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search models…"
                className="w-full bg-transparent text-sm text-[var(--admin-text)] outline-none placeholder:text-[var(--admin-text-dim)]"
              />
            </div>
            <div ref={listRef} className="admin-scroll max-h-[400px] overflow-y-auto p-1">
              {filteredEntries.length === 0 && filteredProviders.length === 0 && (
                <p className="px-3 py-6 text-center text-sm text-[var(--admin-text-muted)]">No results found.</p>
              )}
              {filteredProviders.length > 0 && (
                <div className="px-2 py-1">
                  <p className="admin-label mb-1">Providers</p>
                  {filteredProviders.map((p) => (
                    <button
                      key={`provider-${p.id}`}
                      type="button"
                      onClick={() => { navigate(`/providers/${encodeURIComponent(p.id)}`); setOpen(false); }}
                      className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-left transition-colors hover:bg-white/[0.03]"
                    >
                      <div className="flex h-9 w-9 items-center justify-center rounded-full bg-white/[0.04]">
                        <span className="text-xs font-medium uppercase text-[var(--admin-text-muted)]">{(p.name ?? p.id).charAt(0)}</span>
                      </div>
                      <div className="flex flex-col items-start">
                        <span className="text-sm font-medium text-[var(--admin-text)]">{p.name ?? p.id}</span>
                        <span className="text-xs text-[var(--admin-text-muted)]">{p.id}</span>
                      </div>
                    </button>
                  ))}
                </div>
              )}
              {groups.map(([label, items]) => (
                <div key={label} className="px-2 py-1">
                  <p className="admin-label mb-1">{label}</p>
                  {items.map((entry) => (
                    <button
                      key={entry.id}
                      type="button"
                      onClick={() => { navigate(`/models/${encodeURIComponent(entry.id)}`); setOpen(false); }}
                      className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-left transition-colors hover:bg-white/[0.03]"
                    >
                      <div className="flex h-9 w-9 items-center justify-center rounded-full bg-white/[0.04]">
                        <span className="text-xs font-medium uppercase text-[var(--admin-text-muted)]">{entry.name.charAt(0)}</span>
                      </div>
                      <div className="flex flex-col items-start">
                        <span className="text-sm font-medium text-[var(--admin-text)]">{entry.name}</span>
                        <span className="text-xs text-[var(--admin-text-muted)]">{entry.id}</span>
                      </div>
                    </button>
                  ))}
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ── QuickStartSection ────────────────────────────────────────────────────────
// Tabbed code snippet (cURL / TypeScript / Python / AI SDK) with a copy button.

export function QuickStartSection({ apiKey, onCopy }: { apiKey?: string; onCopy?: () => void }) {
  const [activeTab, setActiveTab] = useState<"curl" | "typescript" | "python" | "ai-sdk">("curl");
  const keyPlaceholder = apiKey ?? "YOUR_API_KEY";

  const curlExample = `curl -X POST https://api.example.com/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer ${keyPlaceholder}" \\
  -d '{
  "model": "auto",
  "messages": [
    {"role": "user", "content": "Hello!"}
  ]
}'`;

  const tsExample = `import OpenAI from "openai";

const client = new OpenAI({
  apiKey: "${keyPlaceholder}",
  baseURL: "https://api.example.com/v1/"
});

const response = await client.chat.completions.create({
  model: "auto",
  messages: [{ role: "user", content: "Hello!" }],
});`;

  const pythonExample = `from openai import OpenAI

client = OpenAI(
    api_key="${keyPlaceholder}",
    base_url="https://api.example.com/v1/",
)

response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Hello!"}],
)`;

  const aiSdkExample = `import { generateText } from "ai";

const { text } = await generateText({
  model: "auto",
  prompt: "Hello!",
});`;

  const code =
    activeTab === "curl" ? curlExample
    : activeTab === "typescript" ? tsExample
    : activeTab === "python" ? pythonExample
    : aiSdkExample;

  function copyCode() {
    void navigator.clipboard.writeText(code);
    onCopy?.();
    console.info("[toast] Copied to clipboard");
  }

  return (
    <div className="admin-card p-5">
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <Code className="h-5 w-5 text-[var(--admin-text-muted)]" />
          <span className="font-medium text-[var(--admin-text)]">Quick Start</span>
        </div>
        <p className="text-sm text-[var(--admin-text-muted)]">
          Use your API key to make requests. wiwi is compatible with the OpenAI SDK — just change the base URL.
        </p>
        <div className="flex gap-2">
          {(["curl", "typescript", "python", "ai-sdk"] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={`admin-btn ${activeTab === tab ? "admin-btn-primary" : "admin-btn-ghost"}`}
            >
              {tab === "curl" ? "cURL" : tab === "typescript" ? "TypeScript" : tab === "python" ? "Python" : "AI SDK"}
            </button>
          ))}
        </div>
        <div className="relative rounded-md border border-[var(--admin-border)] bg-white/[0.02]">
          <button
            type="button"
            onClick={copyCode}
            className="absolute right-2 top-2 rounded p-1.5 text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)]"
            title="Copy code"
          >
            <Copy className="h-3.5 w-3.5" />
            <span className="sr-only">Copy code</span>
          </button>
          <pre className="overflow-x-auto p-4 font-mono text-xs leading-relaxed text-[var(--admin-text-muted)]">
            {code}
          </pre>
        </div>
      </div>
    </div>
  );
}

// ── UsageModeSelector ────────────────────────────────────────────────────────
// Segmented All / Credits / BYOK toggle. Stores the selection in the `mode`
// URL search param so it survives navigation.

const USAGE_MODE_OPTIONS = [
  { value: "total", label: "All" },
  { value: "credits", label: "Credits" },
  { value: "api-keys", label: "BYOK" },
] as const;

type UsageMode = (typeof USAGE_MODE_OPTIONS)[number]["value"];

function parseUsageMode(value: string | null): UsageMode {
  return (USAGE_MODE_OPTIONS.find((o) => o.value === value)?.value ?? "total") as UsageMode;
}

export function useUsageMode(): UsageMode {
  const [searchParams] = useSearchParams();
  return parseUsageMode(searchParams.get("mode"));
}

export function UsageModeSelector({ className }: { className?: string }) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const mode = parseUsageMode(searchParams.get("mode"));

  const setMode = (next: UsageMode) => {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "total") params.delete("mode");
    else params.set("mode", next);
    const query = params.toString();
    navigate(query ? `?${query}` : location.pathname, { replace: true });
  };

  return (
    <div className={`inline-flex items-center rounded-lg border border-[var(--admin-border)] bg-white/[0.02] p-0.5 ${className ?? ""}`}>
      {USAGE_MODE_OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => setMode(option.value)}
          title={
            option.value === "api-keys"
              ? "Usage served by your own provider keys (not billed to credits)"
              : option.value === "credits"
                ? "Usage billed against your credit balance"
                : "All traffic"
          }
          className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
            mode === option.value
              ? "bg-[var(--admin-bg)] text-[var(--admin-text)] shadow-sm"
              : "text-[var(--admin-text-muted)] hover:text-[var(--admin-text)]"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
