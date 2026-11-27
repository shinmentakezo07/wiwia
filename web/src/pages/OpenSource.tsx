// Open Source — how to run the gateway yourself. Every command on this page is
// taken from the repository's own Docker/Compose setup and quickstart. Visual
// vocabulary (grid wash, glows, spotlight cards, pill eyebrow, proof strip) is
// borrowed from the Enterprise showcase so the public pages read as one system.

import { Link } from "react-router-dom";
import type { PointerEvent } from "react";
import { ArrowRight, Check, GitFork, Github, Lock, Minus, Scale, ServerCog, ShieldCheck } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { CopyButton } from "@/components/ui";
import { ClosingCta } from "@/components/ClosingCta";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";
const REPO_URL = "https://github.com/shinmentakezo07/wiwia";

const REASONS: { icon: LucideIcon; title: string; description: string }[] = [
  {
    icon: Lock,
    title: "No vendor lock-in",
    description:
      "The whole platform is MIT-licensed. Fork it, audit it, and run it forever — no proprietary control plane you can be cut off from.",
  },
  {
    icon: ServerCog,
    title: "Self-host the full stack",
    description:
      "Gateway, admin console, and public site ship as one process. Keep every request and key inside your own infrastructure.",
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

const PROOF: { value: string; label: string }[] = [
  { value: "MIT", label: "Licence" },
  { value: "13", label: "Provider types" },
  { value: "1", label: "Process to run" },
  { value: "0", label: "Platform fee" },
];

const FAQS: { question: string; answer: string }[] = [
  {
    question: "Is the gateway really open source?",
    answer:
      "Yes. The entire platform — gateway, API, dashboard, and public site — is licensed under MIT and free to self-host forever. There is no closed control plane and no paid build.",
  },
  {
    question: "What does the MIT license mean for my company?",
    answer:
      "You can run, modify, and redistribute the gateway, including inside a commercial product, as long as the copyright and permission notice are kept. There is no source-availability requirement and no enterprise licence to buy.",
  },
  {
    question: "How do I self-host the WIWI Gateway?",
    answer:
      "Copy wiwi.yaml.example to wiwi.yaml, set WIWI_MASTER_KEY and your provider keys, then run docker compose up --build (or install the package and run wiwi --config wiwi.yaml). The gateway listens on port 4000 and serves the console at the root.",
  },
  {
    question: "Do I need a hosted account?",
    answer:
      "No. Everything runs locally with SQLite by default; point DATABASE_URL at Postgres when you need it. There is no managed billing layer and no platform fee — you pay your providers directly.",
  },
  {
    question: "Which providers does the gateway support?",
    answer:
      "Twelve outbound provider types — OpenAI, Anthropic, Gemini, OpenRouter, NVIDIA NIM, GMI Cloud, B.AI, OpenCode, Cline, Hermes, IFM, and any OpenAI-compatible endpoint — through one OpenAI-compatible inbound API.",
  },
];

const CLOSED_COMPARISON: { name: string; scope: string; selfHost: boolean | "Partial"; us?: boolean }[] = [
  { name: "WIWI Gateway", scope: "Full platform (MIT)", selfHost: true, us: true },
  { name: "OpenRouter", scope: "Closed source", selfHost: false },
  { name: "Vercel AI Gateway", scope: "Closed source", selfHost: false },
  { name: "Cloudflare AI Gateway", scope: "Closed source", selfHost: false },
  { name: "Portkey", scope: "Gateway + parts (MIT)", selfHost: "Partial" },
  { name: "LiteLLM", scope: "Library/proxy (MIT)", selfHost: true },
];

const DOCKER_LINES: { text: string; comment?: boolean }[] = [
  { text: "cp wiwi.yaml.example wiwi.yaml" },
  { text: "# set WIWI_MASTER_KEY and your provider keys in wiwi.yaml", comment: true },
  { text: "docker compose up --build" },
  { text: "" },
  { text: "# or without Docker", comment: true },
  { text: "uv pip install -e ." },
  { text: "wiwi --config wiwi.yaml   # http://localhost:4000" },
];
const DOCKER_COMMAND = DOCKER_LINES.map((l) => l.text).join("\n");

/** Track the pointer so the card's radial spotlight follows it. */
function trackSpotlight(e: PointerEvent<HTMLElement>) {
  const el = e.currentTarget;
  const r = el.getBoundingClientRect();
  el.style.setProperty("--x", `${e.clientX - r.left}px`);
  el.style.setProperty("--y", `${e.clientY - r.top}px`);
}

function SectionIntro(props: { index: string; title: string; body?: string }) {
  return (
    <div className="mb-7 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between sm:gap-8">
      <div>
        <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-blue-300/80">{props.index}</span>
        <h2 className="mt-2 text-[24px] font-semibold tracking-[-0.015em] text-[var(--admin-text)] sm:text-[28px]">
          {props.title}
        </h2>
      </div>
      {props.body && (
        <p className="max-w-sm text-[14px] leading-relaxed text-[var(--admin-text-muted)] sm:text-right">{props.body}</p>
      )}
    </div>
  );
}

/* The hero visual: the licence itself, as a file. It restates the one claim the
   page makes — the permissions are real and short enough to read. Decorative. */
function LicenceCard() {
  const grants = ["Commercial use", "Modification", "Distribution", "Private use"];
  return (
    <div className="relative mx-auto w-full max-w-sm" aria-hidden>
      <div className="wiwi-conic-border shadow-[0_30px_80px_-30px_rgba(59,130,246,0.35)]">
        <div className="relative rounded-[15px] bg-[#070707]/95 p-5 backdrop-blur sm:p-6">
          <div className="mb-5 flex items-center justify-between">
            <div className="flex gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-white/10" />
              <span className="h-2.5 w-2.5 rounded-full bg-white/10" />
              <span className="h-2.5 w-2.5 rounded-full bg-white/10" />
            </div>
            <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--admin-text-dim)]">LICENSE</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-blue-400/30 bg-blue-500/15 text-blue-200">
              <Scale size={17} />
            </span>
            <div>
              <p className="text-[15px] font-semibold text-[var(--admin-text)]">MIT License</p>
              <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--admin-text-dim)]">gateway · console · site</p>
            </div>
          </div>
          <ul className="mt-5 space-y-2">
            {grants.map((g) => (
              <li key={g} className="flex items-center gap-2.5 rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-[13px] text-[var(--admin-text-muted)]">
                <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-emerald-400/10 ring-1 ring-emerald-400/25">
                  <Check size={10} strokeWidth={3} className="text-emerald-400" />
                </span>
                {g}
              </li>
            ))}
          </ul>
          <p className="mt-4 border-t border-white/[0.06] pt-3 font-mono text-[10.5px] leading-relaxed text-[var(--admin-text-dim)]">
            Condition: keep the copyright and permission notice.
          </p>
        </div>
      </div>
    </div>
  );
}

export function OpenSourcePage() {
  return (
    <div className="mx-auto max-w-6xl space-y-20 pb-16 sm:space-y-24">
      {/* ── hero ── */}
      <section className="relative overflow-hidden rounded-[24px] border border-[var(--admin-border)] bg-[var(--admin-surface)] sm:rounded-[32px]">
        <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
          <div className="hero-grid absolute inset-x-0 top-0 h-[620px] opacity-70" />
          <div
            className="hero-orb-1 absolute -left-28 -top-32 h-[460px] w-[460px] rounded-full"
            style={{ background: "radial-gradient(circle, rgba(59,130,246,0.18) 0%, transparent 62%)" }}
          />
          <div
            className="hero-orb-2 absolute -right-32 -top-16 h-[420px] w-[420px] rounded-full"
            style={{ background: "radial-gradient(circle, rgba(139,92,246,0.16) 0%, transparent 62%)" }}
          />
        </div>
        <div className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-70" aria-hidden />

        <div className="relative grid items-center gap-12 px-6 py-14 sm:px-10 sm:py-16 lg:grid-cols-[1.15fr_1fr] lg:gap-14 lg:px-14 lg:py-20">
          <div className="text-center lg:text-left">
            <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-blue-500/20 bg-blue-500/[0.06] px-3.5 py-1.5 text-blue-300">
              <GitFork size={12} aria-hidden />
              <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.2em]">MIT · Self-hostable</span>
            </div>
            <h1 className="text-3xl font-semibold leading-[1.08] tracking-[-0.025em] text-[var(--admin-text)] sm:text-4xl lg:text-[52px]">
              The open source
              <br />
              <span className="hero-gradient-text">WIWI Gateway</span>
            </h1>
            <p className="mx-auto mt-5 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)] sm:text-base lg:mx-0">
              Route every dialect to thirteen provider types through one API — and run the
              entire platform on your own infrastructure. Open source, self-hostable, no lock-in.
            </p>
            <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row lg:justify-start">
              <Link
                to="/signup"
                className="group inline-flex h-11 w-full items-center justify-center gap-2 rounded-full bg-gradient-to-b from-brand-400 to-brand-700 px-7 text-sm font-semibold text-white shadow-lg shadow-brand-600/25 transition-[transform,filter] duration-150 hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400/60 active:scale-[0.98] sm:w-auto"
              >
                Start free
                <ArrowRight size={15} className="transition-transform duration-150 group-hover:translate-x-0.5" />
              </Link>
              <a
                href={REPO_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.02] px-6 text-sm font-medium text-[var(--admin-text)] backdrop-blur transition-colors hover:border-white/[0.16] hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/20 sm:w-auto"
              >
                <Github size={15} aria-hidden />
                Star on GitHub
                <span className="sr-only">(opens in a new tab)</span>
              </a>
            </div>
          </div>
          <LicenceCard />
        </div>

        {/* proof strip */}
        <div className="relative grid grid-cols-2 gap-px border-t border-[var(--admin-border)] bg-[var(--admin-border)] sm:grid-cols-4">
          {PROOF.map((stat) => (
            <div key={stat.label} className="bg-[var(--admin-surface)] px-5 py-6 text-center">
              <p className="admin-stat-value text-2xl sm:text-[28px]">{stat.value}</p>
              <p className="admin-label mt-1.5">{stat.label}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── reasons ── */}
      <section className="scroll-reveal">
        <SectionIntro
          index=".01 · why it matters"
          title="Why an open source AI gateway matters"
          body="The infrastructure routing every model call is too important to be a black box."
        />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {REASONS.map((reason, i) => {
            const Icon = reason.icon;
            return (
              <div
                key={reason.title}
                onPointerMove={trackSpotlight}
                className="group relative overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-white/[0.015] p-5 transition-[border-color,background-color,transform] duration-300 hover:-translate-y-0.5 hover:border-blue-500/25 hover:bg-white/[0.03] motion-reduce:hover:translate-y-0"
              >
                <div className="ent-spotlight pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover:opacity-100" aria-hidden />
                <div className="relative">
                  <div className="mb-5 flex items-start justify-between">
                    <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-white/[0.06] bg-gradient-to-br from-blue-500/15 to-violet-500/10 text-blue-300 transition-colors duration-300 group-hover:border-blue-500/30 group-hover:text-blue-200">
                      <Icon size={17} aria-hidden />
                    </span>
                    <span className="font-mono text-[10px] tabular-nums tracking-[0.18em] text-[var(--admin-text-dim)]" aria-hidden>
                      0{i + 1}
                    </span>
                  </div>
                  <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">{reason.title}</h3>
                  <p className="mt-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{reason.description}</p>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* ── self-host + comparison, side by side on desktop ── */}
      <div className="grid gap-16 lg:grid-cols-2 lg:gap-10">
        <section className="scroll-reveal min-w-0">
          <SectionIntro index=".02 · self-host" title="Self-host in one command" />
          <div className="overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[#070707]">
            <div className="flex items-center justify-between gap-2 border-b border-[var(--admin-border)] px-4 py-2">
              <div className="flex items-center gap-2">
                <span className="h-2.5 w-2.5 rounded-full bg-white/10" aria-hidden />
                <span className="h-2.5 w-2.5 rounded-full bg-white/10" aria-hidden />
                <span className="h-2.5 w-2.5 rounded-full bg-white/10" aria-hidden />
                <span className="ml-2 font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--admin-text-dim)]">terminal</span>
              </div>
              <CopyButton text={DOCKER_COMMAND} className="min-h-11" />
            </div>
            <pre className="overflow-x-auto p-5 text-[12.5px] leading-[1.8]" style={{ fontFamily: MONO }}>
              <code>
                {DOCKER_LINES.map((line, i) => (
                  <span key={i} className="block">
                    {line.text === "" ? "\u00a0" : line.comment ? (
                      <span className="text-[var(--admin-text-dim)]">{line.text}</span>
                    ) : (
                      <>
                        <span aria-hidden className="select-none text-blue-400/90">$ </span>
                        <span className="text-zinc-200">{line.text}</span>
                      </>
                    )}
                  </span>
                ))}
              </code>
            </pre>
          </div>
          <p className="mt-4 text-[13px] text-[var(--admin-text-muted)]">
            Gateway, console, and public site in one process. The full configuration reference is in{" "}
            <Link to="/docs" className="rounded text-[var(--admin-text)] underline decoration-white/20 underline-offset-4 hover:decoration-white/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/60">
              the docs
            </Link>
            .
          </p>
        </section>

        <section className="scroll-reveal min-w-0">
          <SectionIntro index=".03 · landscape" title="Open vs closed gateways" />
          <div className="overflow-x-auto rounded-2xl border border-[var(--admin-border)]">
            <table className="w-full min-w-[340px] text-left text-[13px]">
              <caption className="sr-only">Open-source scope and self-hosting across LLM gateways</caption>
              <thead className="bg-white/[0.02]">
                <tr>
                  <th scope="col" className="admin-label px-4 py-3">Gateway</th>
                  <th scope="col" className="admin-label px-4 py-3">Open-source scope</th>
                  <th scope="col" className="admin-label px-4 py-3 text-center">Self-host</th>
                </tr>
              </thead>
              <tbody>
                {CLOSED_COMPARISON.map((row) => (
                  <tr
                    key={row.name}
                    className={`border-t border-[var(--admin-border)] ${row.us ? "bg-blue-500/[0.05]" : ""}`}
                  >
                    <th scope="row" className="px-4 py-3 font-medium text-[var(--admin-text)]">
                      <span className="flex items-center gap-2">
                        {row.us && <span className="h-4 w-0.5 rounded-full bg-blue-400" aria-hidden />}
                        {row.name}
                      </span>
                    </th>
                    <td className="px-4 py-3 text-[var(--admin-text-muted)]">{row.scope}</td>
                    <td className="px-4 py-3 text-center">
                      {row.selfHost === true ? (
                        <>
                          <Check className="inline h-4 w-4 text-emerald-400" aria-hidden />
                          <span className="sr-only">Yes</span>
                        </>
                      ) : row.selfHost === false ? (
                        <>
                          <Minus className="inline h-4 w-4 text-[var(--admin-text-dim)]" aria-hidden />
                          <span className="sr-only">No</span>
                        </>
                      ) : (
                        <span className="text-amber-300/90">{row.selfHost}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {/* The comparison lives on one page that covers every vendor; /compare/<vendor>
              has no route and would bounce to the landing page. */}
          <p className="mt-4 text-[13px] text-[var(--admin-text-muted)]">
            Most gateways open-source a router at best. We open-source the whole platform.{" "}
            <Link to="/compare" className="inline-flex items-center gap-1 rounded text-[var(--admin-text)] underline decoration-white/20 underline-offset-4 hover:decoration-white/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/60">
              Full comparison <ArrowRight size={12} aria-hidden />
            </Link>
          </p>
        </section>
      </div>

      {/* ── FAQ: sticky heading on desktop, accordion list beside it ── */}
      <section className="scroll-reveal grid gap-8 lg:grid-cols-[1fr_2fr] lg:gap-14">
        <div className="lg:sticky lg:top-24 lg:self-start">
          <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-blue-300/80">.04 · faq</span>
          <h2 className="mt-2 text-[24px] font-semibold tracking-[-0.015em] text-[var(--admin-text)] sm:text-[28px]">
            Licensing &amp; self-hosting
          </h2>
          <p className="mt-3 text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            Still unsure?{" "}
            <Link to="/contact" className="rounded text-[var(--admin-text)] underline decoration-white/20 underline-offset-4 hover:decoration-white/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/60">
              Ask us directly
            </Link>
            .
          </p>
        </div>
        <div className="divide-y divide-[var(--admin-border)] rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)]">
          {FAQS.map((item) => (
            <details key={item.question} className="group [&_summary::-webkit-details-marker]:hidden">
              <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-4 rounded-2xl px-5 py-4 text-[14px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.02] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/60">
                {item.question}
                <span
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-white/[0.08] text-[var(--admin-text-dim)] transition-transform duration-200 group-open:rotate-45 group-open:border-blue-400/30 group-open:text-blue-300 motion-reduce:transition-none"
                  aria-hidden
                >
                  +
                </span>
              </summary>
              <p className="px-5 pb-5 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{item.answer}</p>
            </details>
          ))}
        </div>
      </section>

      <ClosingCta
        eyebrow=".05 · get started"
        title="Own the whole stack, not a seat on someone else's."
        body="Gateway, console, and public site ship as one process under MIT. Clone it, run it, and keep every request and key inside your own infrastructure."
        primary={{ label: "Read the quickstart", to: "/docs" }}
        secondary={{ label: "See pricing", to: "/pricing" }}
        footnote="No proprietary control plane. No seat licence. Fork it if you outgrow it."
        commands={[
          { title: "Clone the repository", command: `git clone ${REPO_URL} && cd wiwia` },
          { title: "Prepare the config", command: "cp wiwi.yaml.example wiwi.yaml" },
          { title: "Start the gateway", command: "wiwi --config wiwi.yaml" },
        ]}
      />
    </div>
  );
}
