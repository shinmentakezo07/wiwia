// ModelsSupported — model listing grouped by provider with a provider filter.
// Ported from the Next.js reference's models-supported.tsx. The reference used
// the @llmgateway/models catalog and a shared ModelCard; this port inlines a
// representative set of providers and models so it is self-contained, and
// renders simple model cards inline. Uses react-router for navigation.

import {
  Braces,
  ExternalLink,
  Eye,
  GitBranch,
  ImagePlus,
  MessageSquare,
  Plus,
  Wrench,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

// ── Inline data (hardcoded subset of the @llmgateway/models catalog) ───────

interface ProviderModelMapping {
  providerId: string;
  externalId: string;
  contextSize?: number | null;
  streaming?: boolean | null;
  vision?: boolean | null;
  tools?: boolean | null;
  reasoning?: boolean | null;
  jsonOutput?: boolean | null;
  inputPrice?: number | null;
  outputPrice?: number | null;
}

interface ModelDefinition {
  id: string;
  name: string;
  family: string;
  output?: string | null;
  providers: ProviderModelMapping[];
}

interface ProviderDefinition {
  id: string;
  name: string;
}

const PROVIDERS: ProviderDefinition[] = [
  { id: "anthropic", name: "Anthropic" },
  { id: "openai", name: "OpenAI" },
  { id: "google", name: "Google" },
  { id: "xai", name: "xAI" },
  { id: "deepseek", name: "DeepSeek" },
  { id: "groq", name: "Groq" },
];

const MODELS: ModelDefinition[] = [
  {
    id: "claude-opus-4.1",
    name: "Claude Opus 4.1",
    family: "Claude",
    output: "text",
    providers: [
      { providerId: "anthropic", externalId: "claude-opus-4.1-20250805", contextSize: 200000, streaming: true, vision: true, tools: true, reasoning: true, jsonOutput: true, inputPrice: 15, outputPrice: 75 },
    ],
  },
  {
    id: "claude-sonnet-4.5",
    name: "Claude Sonnet 4.5",
    family: "Claude",
    output: "text",
    providers: [
      { providerId: "anthropic", externalId: "claude-sonnet-4.5-20250929", contextSize: 200000, streaming: true, vision: true, tools: true, reasoning: true, jsonOutput: true, inputPrice: 3, outputPrice: 15 },
    ],
  },
  {
    id: "claude-haiku-4.5",
    name: "Claude Haiku 4.5",
    family: "Claude",
    output: "text",
    providers: [
      { providerId: "anthropic", externalId: "claude-haiku-4.5-20251001", contextSize: 200000, streaming: true, vision: true, tools: true, jsonOutput: true, inputPrice: 1, outputPrice: 5 },
    ],
  },
  {
    id: "gpt-5",
    name: "GPT-5",
    family: "GPT",
    output: "text",
    providers: [
      { providerId: "openai", externalId: "gpt-5", contextSize: 400000, streaming: true, vision: true, tools: true, reasoning: true, jsonOutput: true, inputPrice: 1.25, outputPrice: 10 },
    ],
  },
  {
    id: "gpt-5-mini",
    name: "GPT-5 Mini",
    family: "GPT",
    output: "text",
    providers: [
      { providerId: "openai", externalId: "gpt-5-mini", contextSize: 400000, streaming: true, vision: true, tools: true, reasoning: true, jsonOutput: true, inputPrice: 0.25, outputPrice: 2 },
    ],
  },
  {
    id: "o4-mini",
    name: "o4-mini",
    family: "o-series",
    output: "text",
    providers: [
      { providerId: "openai", externalId: "o4-mini", contextSize: 200000, streaming: true, vision: true, tools: true, reasoning: true, jsonOutput: true, inputPrice: 1.1, outputPrice: 4.4 },
    ],
  },
  {
    id: "gemini-2.5-pro",
    name: "Gemini 2.5 Pro",
    family: "Gemini",
    output: "text",
    providers: [
      { providerId: "google", externalId: "gemini-2.5-pro", contextSize: 1048576, streaming: true, vision: true, tools: true, reasoning: true, jsonOutput: true, inputPrice: 1.25, outputPrice: 10 },
    ],
  },
  {
    id: "gemini-2.5-flash",
    name: "Gemini 2.5 Flash",
    family: "Gemini",
    output: "text",
    providers: [
      { providerId: "google", externalId: "gemini-2.5-flash", contextSize: 1048576, streaming: true, vision: true, tools: true, jsonOutput: true, inputPrice: 0.075, outputPrice: 0.3 },
    ],
  },
  {
    id: "grok-4",
    name: "Grok 4",
    family: "Grok",
    output: "text",
    providers: [
      { providerId: "xai", externalId: "grok-4", contextSize: 256000, streaming: true, vision: true, tools: true, reasoning: true, inputPrice: 3, outputPrice: 15 },
    ],
  },
  {
    id: "grok-code-fast",
    name: "Grok Code Fast",
    family: "Grok",
    output: "text",
    providers: [
      { providerId: "xai", externalId: "grok-code-fast-1", contextSize: 256000, streaming: true, tools: true, reasoning: true, inputPrice: 0.2, outputPrice: 1.5 },
    ],
  },
  {
    id: "deepseek-v3.2",
    name: "DeepSeek V3.2",
    family: "DeepSeek",
    output: "text",
    providers: [
      { providerId: "deepseek", externalId: "deepseek-v3.2-exp", contextSize: 128000, streaming: true, tools: true, jsonOutput: true, inputPrice: 0.27, outputPrice: 1.1 },
    ],
  },
  {
    id: "deepseek-r1",
    name: "DeepSeek R1",
    family: "DeepSeek",
    output: "text",
    providers: [
      { providerId: "deepseek", externalId: "deepseek-reasoner", contextSize: 163000, streaming: true, reasoning: true, jsonOutput: true, inputPrice: 0.55, outputPrice: 2.19 },
    ],
  },
  {
    id: "llama-3.3-70b",
    name: "Llama 3.3 70B",
    family: "Llama",
    output: "text",
    providers: [
      { providerId: "groq", externalId: "llama-3.3-70b-versatile", contextSize: 128000, streaming: true, tools: true, jsonOutput: true, inputPrice: 0.59, outputPrice: 0.99 },
    ],
  },
  {
    id: "gpt-image-1",
    name: "GPT Image 1",
    family: "GPT",
    output: "image",
    providers: [
      { providerId: "openai", externalId: "gpt-image-1", contextSize: null, streaming: false, inputPrice: 5, outputPrice: 40 },
    ],
  },
];

// ── Provider icon — simple colored badge by provider id ────────────────────

const PROVIDER_COLORS: Record<string, string> = {
  anthropic: "#d4a27f",
  openai: "#10a37f",
  google: "#4285f4",
  xai: "#e2e2e2",
  deepseek: "#4d6bfa",
  groq: "#f55036",
};

function ProviderBadge({ providerId, size = 40 }: { providerId: string; size?: number }) {
  const color = PROVIDER_COLORS[providerId] ?? "#888";
  const letter = PROVIDERS.find((p) => p.id === providerId)?.name?.charAt(0) ?? providerId.charAt(0).toUpperCase();
  return (
    <div
      className="flex items-center justify-center rounded font-bold text-black"
      style={{ height: size, width: size, background: color }}
    >
      {letter}
    </div>
  );
}

// ── Capability icons ───────────────────────────────────────────────────────

function getCapabilityIcons(mapping: ProviderModelMapping, output?: string | null) {
  const caps: Array<{ icon: LucideIcon; label: string; color: string }> = [];
  if (mapping.streaming) caps.push({ icon: Zap, label: "Streaming", color: "text-blue-500" });
  if (mapping.vision) caps.push({ icon: Eye, label: "Vision", color: "text-green-500" });
  if (mapping.tools) caps.push({ icon: Wrench, label: "Tools", color: "text-purple-500" });
  if (mapping.reasoning) caps.push({ icon: MessageSquare, label: "Reasoning", color: "text-orange-500" });
  if (mapping.jsonOutput) caps.push({ icon: Braces, label: "JSON Output", color: "text-cyan-500" });
  if (output?.includes("image")) caps.push({ icon: ImagePlus, label: "Image Generation", color: "text-pink-500" });
  return caps;
}

function formatPrice(price?: number | null) {
  if (price == null) return "—";
  return `$${(price * 1e6).toFixed(2)}`;
}

function formatContextSize(n?: number | null) {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(0)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

// ── Grouping ──────────────────────────────────────────────────────────────

const groupedProviders = MODELS.reduce<Record<string, ModelDefinition[]>>((acc, def) => {
  def.providers.forEach((map) => {
    const provider = PROVIDERS.find((p) => p.id === map.providerId)!;
    if (!acc[provider.name]) acc[provider.name] = [];
    acc[provider.name].push(def);
  });
  return acc;
}, {});

const sortedProviderEntries = Object.entries(groupedProviders)
  .sort(([a], [b]) => a.localeCompare(b))
  .map(([providerName, models]) => [providerName, [...models].reverse()]) as [string, ModelDefinition[]][];

const totalModels = MODELS.length;
const totalProviders = sortedProviderEntries.length;

// ── Model card (inline) ────────────────────────────────────────────────────

function ModelCardRow({ model, navigate }: { model: ModelDefinition; navigate: (path: string) => void }) {
  const provider = model.providers[0];
  const caps = getCapabilityIcons(provider, model.output);
  return (
    <div
      onClick={() => navigate(`/models`)}
      className="group cursor-pointer rounded-lg border border-[var(--admin-border)] bg-[var(--admin-surface)] p-4 transition-colors hover:border-[var(--admin-border-hover)]"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-base font-semibold text-[var(--admin-text)]">{model.name}</p>
          <p className="truncate text-xs text-[var(--admin-text-muted)]">{provider.externalId}</p>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-3">
        {caps.map((c) => (
          <span key={c.label} className="flex items-center gap-1 text-[11px] text-[var(--admin-text-muted)]" title={c.label}>
            <c.icon className={`h-3.5 w-3.5 ${c.color}`} />
            {c.label}
          </span>
        ))}
      </div>
      <div className="mt-3 flex items-center justify-between border-t border-[var(--admin-border)] pt-3 text-xs text-[var(--admin-text-muted)]">
        <span>Context: <span className="font-mono font-bold text-[var(--admin-text)]">{formatContextSize(provider.contextSize)}</span></span>
        <span>
          {formatPrice(provider.inputPrice)} in / {formatPrice(provider.outputPrice)} out
        </span>
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

export function ModelsSupported({ isDashboard }: { isDashboard?: boolean }) {
  const navigate = useNavigate();
  const [selectedProvider, setSelectedProvider] = useState<string>("all");

  const filteredProviderEntries =
    selectedProvider === "all"
      ? sortedProviderEntries
      : sortedProviderEntries.filter(([providerName]) => providerName === selectedProvider);

  const filteredModelsCount = filteredProviderEntries.reduce((sum, [, models]) => sum + models.length, 0);
  const filteredProvidersCount = filteredProviderEntries.length;

  return (
    <div className={isDashboard ? "" : "container mx-auto px-4 pt-60 pb-8"}>
      {!isDashboard ? (
        <header className="mb-12 text-center">
          <h1 className="mb-4 text-4xl font-bold tracking-tight">Supported AI Providers &amp; Models</h1>
          <p className="mx-auto mb-6 max-w-3xl text-xl text-[var(--admin-text-muted)]">
            Access {totalModels} models from {totalProviders} leading AI providers through our unified API
          </p>
          <div className="mb-8 flex justify-center gap-8 text-sm text-[var(--admin-text-muted)]">
            <div className="flex items-center gap-2">
              <div className="h-2 w-2 rounded-full bg-green-500" />
              <span>{totalProviders} Providers</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="h-2 w-2 rounded-full bg-blue-500" />
              <span>{totalModels} Models</span>
            </div>
          </div>
          <div className="flex flex-col justify-center gap-4 md:flex-row">
            <a
              href="https://github.com/issues/new"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 rounded-md border border-[var(--admin-border)] px-4 py-2 text-sm text-[var(--admin-text)] transition-colors hover:border-[var(--admin-border-hover)]"
            >
              <Plus className="h-4 w-4" /> Request New Model
            </a>
            <a
              href="https://github.com/issues/new"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 rounded-md border border-[var(--admin-border)] px-4 py-2 text-sm text-[var(--admin-text)] transition-colors hover:border-[var(--admin-border-hover)]"
            >
              <GitBranch className="h-4 w-4" /> Request New Provider
            </a>
          </div>
        </header>
      ) : (
        <div className="mb-10">
          <div className="mb-6 flex flex-col items-start justify-between md:flex-row md:items-center">
            <p className="max-w-3xl text-xl text-[var(--admin-text-muted)]">
              Access {totalModels} models from {totalProviders} leading AI providers through our unified API
            </p>
            <div className="mt-4 flex gap-2 md:mt-0">
              <a
                href="https://github.com/issues/new"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 rounded-md border border-[var(--admin-border)] px-3 py-1.5 text-sm text-[var(--admin-text)] transition-colors hover:border-[var(--admin-border-hover)]"
              >
                <Plus className="h-4 w-4" /> Request Model
              </a>
              <a
                href="https://github.com/issues/new"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 rounded-md border border-[var(--admin-border)] px-3 py-1.5 text-sm text-[var(--admin-text)] transition-colors hover:border-[var(--admin-border-hover)]"
              >
                <GitBranch className="h-4 w-4" /> Request Provider
              </a>
            </div>
          </div>
          <div className="flex justify-start gap-8 text-sm text-[var(--admin-text-muted)]">
            <div className="flex items-center gap-2">
              <div className="h-2 w-2 rounded-full bg-green-500" />
              <span>{totalProviders} Providers</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="h-2 w-2 rounded-full bg-blue-500" />
              <span>{totalModels} Models</span>
            </div>
          </div>
        </div>
      )}

      {/* Filter */}
      <div className="mb-8">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            {selectedProvider !== "all" && (
              <div className="flex gap-4 text-sm text-[var(--admin-text-muted)]">
                <div className="flex items-center gap-2">
                  <div className="h-2 w-2 rounded-full bg-blue-500" />
                  <span>
                    Showing {filteredModelsCount} models from {filteredProvidersCount} provider
                  </span>
                </div>
              </div>
            )}
          </div>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-[var(--admin-text-muted)]">Filter by Provider:</span>
            </div>
            <select
              value={selectedProvider}
              onChange={(e) => setSelectedProvider(e.target.value)}
              className="admin-input w-[200px]"
            >
              <option value="all">All Providers</option>
              {sortedProviderEntries.map(([providerName, models]) => {
                const providerId = models[0].providers[0].providerId;
                return (
                  <option key={providerName} value={providerName}>
                    {providerName} ({PROVIDERS.find((p) => p.id === providerId)?.name ?? providerId})
                  </option>
                );
              })}
            </select>
            {selectedProvider !== "all" && (
              <button
                type="button"
                onClick={() => setSelectedProvider("all")}
                className="admin-btn admin-btn-ghost"
              >
                Clear Filter
              </button>
            )}
          </div>
        </div>
      </div>

      <section className="space-y-12">
        {filteredProviderEntries.map(([providerName, models]) => {
          const providerId = models[0].providers[0].providerId;
          return (
            <div key={providerName} className="space-y-6">
              <Link
                to={`/providers/${providerId}`}
                className="flex items-center gap-3 transition-opacity hover:opacity-80"
              >
                <ProviderBadge providerId={providerId} />
                <h2 className="text-2xl font-semibold">{providerName}</h2>
                <span className="text-sm text-[var(--admin-text-muted)]">
                  {models.length} model{models.length !== 1 && "s"}
                </span>
              </Link>
              <div className="grid gap-4 sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-3">
                {models.map((model, index) => (
                  <ModelCardRow
                    key={`${providerId}-${model.id}-${index}`}
                    model={model}
                    navigate={navigate}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </section>

      {filteredProviderEntries.length === 0 && (
        <div className="py-12 text-center">
          <p className="text-[var(--admin-text-muted)]">No providers match the selected filter.</p>
        </div>
      )}

      <footer className="mt-16 text-center">
        <a
          href="/docs"
          target="_blank"
          className="inline-flex items-center gap-2 text-sm text-[var(--admin-text-muted)]"
        >
          <span>Data sourced from the wiwi model catalog</span>
          <ExternalLink className="h-4 w-4" />
        </a>
      </footer>
    </div>
  );
}

export { MODELS as MODEL_DEFINITIONS, PROVIDERS as PROVIDER_DEFINITIONS };
