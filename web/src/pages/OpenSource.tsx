// Open Source — the open source LLM gateway. Adapted from the llmgateway.io
// open-source page with inlined data, in the dark design system.

import { Link } from "react-router-dom";
import { ArrowRight, Check, GitFork, Lock, ServerCog, ShieldCheck } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge, Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const REASONS: { icon: LucideIcon; title: string; description: string }[] = [
  {
    icon: Lock,
    title: "No vendor lock-in",
    description:
      "The whole platform is AGPLv3. Fork it, audit it, and run it forever — no proprietary control plane you can be cut off from.",
  },
  {
    icon: ServerCog,
    title: "Self-host the full stack",
    description:
      "Gateway, dashboard, and worker ship in a single Docker image. Keep every request and key inside your own infrastructure.",
  },
  {
    icon: ShieldCheck,
    title: "Data residency by default",
    description:
      "For regulated and privacy-sensitive teams, requests never have to leave your network or pass through a third party.",
  },
  {
    icon: GitFork,
    title: "Inspect and extend",
    description:
      "Read the code, open a PR, or bend it to your stack. An LLM API gateway you can actually change beats one you can only call.",
  },
];

const FAQS: { question: string; answer: string }[] = [
  {
    question: "Is the gateway really open source?",
    answer:
      "Yes. The entire platform — gateway, API, dashboard, and worker — is licensed under AGPLv3 and free to self-host forever. Most alternatives only open-source a thin router, or nothing at all.",
  },
  {
    question: "What does the AGPLv3 license mean for my company?",
    answer:
      "You can run the gateway internally and in production for free. AGPLv3's source-availability requirement applies when you offer a modified version to others as a network service. For commercial terms outside AGPLv3, an enterprise license is available.",
  },
  {
    question: "How do I self-host the LLM gateway?",
    answer:
      "One Docker command runs the unified image with the gateway, dashboard, and worker. Point your OpenAI-compatible client at your own deployment and you are live — no managed account required.",
  },
  {
    question: "Is there a managed option too?",
    answer:
      "Yes. If you would rather not run infrastructure, the hosted gateway is pay-as-you-go with a flat 5% platform fee on credits, or 0% when you bring your own provider keys. Optional full data retention is billed separately.",
  },
  {
    question: "Which models does the open-source gateway support?",
    answer:
      "200+ models across 40+ providers — OpenAI, Anthropic, Google, Mistral, Llama and more — through one OpenAI-compatible endpoint, whether you self-host or use the managed service.",
  },
];

const CLOSED_COMPARISON: { name: string; scope: string; selfHost: boolean | "Partial" }[] = [
  { name: "LLM Gateway", scope: "Full platform (AGPLv3)", selfHost: true },
  { name: "OpenRouter", scope: "Closed source", selfHost: false },
  { name: "Vercel AI Gateway", scope: "Closed source", selfHost: false },
  { name: "Cloudflare AI Gateway", scope: "Closed source", selfHost: false },
  { name: "Portkey", scope: "Gateway + parts (MIT)", selfHost: "Partial" },
  { name: "LiteLLM", scope: "Library/proxy (MIT)", selfHost: true },
];

const DOCKER_COMMAND = `docker run -d \\
  --name llmgateway \\
  -p 3002:3002 -p 4001:4001 -p 4002:4002 \\
  -e AUTH_SECRET="your-secret" \\
  -e GATEWAY_API_KEY_HASH_SECRET="your-hash-secret" \\
  ghcr.io/theopenco/llmgateway-unified:latest`;

export function OpenSourcePage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="text-center">
        <Badge tone="violet">AGPLv3 · Self-hostable</Badge>
        <h1 className="mt-4 text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          The Open Source{" "}
          <span className="bg-gradient-to-r from-violet-400 to-fuchsia-400 bg-clip-text text-transparent">
            LLM Gateway
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          Route 200+ models across 40+ providers through one OpenAI-compatible API — and
          run the entire platform on your own infrastructure. Open source,
          self-hostable, no lock-in.
        </p>
        <div className="mt-6 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link
            to="/signup"
            className="inline-flex items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 py-3 text-[14px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] hover:brightness-110"
          >
            Start free
            <ArrowRight size={16} />
          </Link>
          <a
            href="https://github.com/shinmentakezo07/wiwia"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-6 py-3 text-[14px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
          >
            Star on GitHub
          </a>
        </div>
      </section>

      {/* ── reasons ── */}
      <section>
        <div className="mb-6 text-center">
          <h2 className="text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Why an open source AI gateway matters
          </h2>
          <p className="mt-2 text-[14px] text-[var(--admin-text-muted)]">
            The infrastructure routing every model call is too important to be a black box.
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          {REASONS.map((reason) => {
            const Icon = reason.icon;
            return (
              <Card key={reason.title} className="p-5 transition-colors hover:border-[var(--admin-border-hover)]">
                <Icon className="mb-3 h-6 w-6 text-blue-400" />
                <h3 className="mb-1 text-[15px] font-semibold text-[var(--admin-text)]">{reason.title}</h3>
                <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{reason.description}</p>
              </Card>
            );
          })}
        </div>
      </section>

      {/* ── docker command ── */}
      <section>
        <div className="mb-4 text-center">
          <h2 className="text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Self-host in one command
          </h2>
          <p className="mt-1 text-[14px] text-[var(--admin-text-muted)]">
            The gateway, dashboard, and worker in a single image.
          </p>
        </div>
        <div className="overflow-hidden rounded-xl border border-[var(--admin-border)] bg-zinc-950">
          <div className="flex items-center gap-2 border-b border-[var(--admin-border)] px-4 py-3">
            <span className="h-3 w-3 rounded-full bg-[#ff5f57]" />
            <span className="h-3 w-3 rounded-full bg-[#febc2e]" />
            <span className="h-3 w-3 rounded-full bg-[#28c840]" />
            <span className="ml-2 font-mono text-[12px] text-[var(--admin-text-dim)]">Terminal</span>
          </div>
          <pre className="overflow-x-auto p-5">
            <code className="font-mono text-[13px] leading-relaxed text-zinc-200" style={{ fontFamily: MONO }}>
              {DOCKER_COMMAND}
            </code>
          </pre>
        </div>
        <p className="mt-4 text-center text-[13px] text-[var(--admin-text-muted)]">
          Prefer not to run infrastructure? The{" "}
          <Link to="/pricing" className="underline">
            managed gateway
          </Link>{" "}
          is pay-as-you-go, or free with your own provider keys.
        </p>
      </section>

      {/* ── comparison table ── */}
      <section>
        <div className="mb-4 text-center">
          <h2 className="text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Open vs closed gateways
          </h2>
          <p className="mt-1 text-[14px] text-[var(--admin-text-muted)]">
            Most gateways open-source a router at best. We open-source the whole platform.
          </p>
        </div>
        <div className="overflow-hidden rounded-xl border border-[var(--admin-border)]">
          <table className="w-full text-left text-[13px]">
            <thead className="bg-white/[0.02]">
              <tr>
                <th className="px-4 py-3 font-semibold text-[var(--admin-text)]">Gateway</th>
                <th className="px-4 py-3 font-semibold text-[var(--admin-text)]">Open-source scope</th>
                <th className="px-4 py-3 text-center font-semibold text-[var(--admin-text)]">Self-host</th>
              </tr>
            </thead>
            <tbody>
              {CLOSED_COMPARISON.map((row) => (
                <tr key={row.name} className="border-t border-[var(--admin-border)]">
                  <td className="px-4 py-3 font-medium text-[var(--admin-text)]">{row.name}</td>
                  <td className="px-4 py-3 text-[var(--admin-text-muted)]">{row.scope}</td>
                  <td className="px-4 py-3 text-center">
                    {row.selfHost === true ? (
                      <Check className="inline h-4 w-4 text-emerald-400" />
                    ) : row.selfHost === false ? (
                      <span className="text-[var(--admin-text-muted)]">No</span>
                    ) : (
                      <span className="text-[var(--admin-text-muted)]">{row.selfHost}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-5 flex flex-wrap gap-3 justify-center text-[13px]">
          <Link to="/compare/open-router" className="underline">vs OpenRouter</Link>
          <Link to="/compare/vercel-ai-gateway" className="underline">vs Vercel AI Gateway</Link>
          <Link to="/compare/portkey" className="underline">vs Portkey</Link>
          <Link to="/compare/litellm" className="underline">vs LiteLLM</Link>
        </div>
      </section>

      {/* ── FAQ ── */}
      <section>
        <div className="mb-6 text-center">
          <span className="admin-label">Open source FAQ</span>
          <h2 className="mt-2 text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Licensing & self-hosting
          </h2>
        </div>
        <div className="space-y-3">
          {FAQS.map((item) => (
            <details
              key={item.question}
              className="group rounded-[12px] border border-[var(--admin-border)] bg-[var(--admin-surface)] transition-colors hover:border-[var(--admin-border-hover)] [&_summary]:list-none"
            >
              <summary className="flex cursor-pointer items-center justify-between gap-3 px-5 py-4 text-[14px] font-medium text-[var(--admin-text)]">
                {item.question}
                <span className="text-[var(--admin-text-dim)] transition-transform duration-150 group-open:rotate-45">
                  <span className="text-[18px] leading-none">+</span>
                </span>
              </summary>
              <div className="border-t border-[var(--admin-border)] px-5 py-4">
                <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{item.answer}</p>
              </div>
            </details>
          ))}
        </div>
      </section>
    </div>
  );
}
