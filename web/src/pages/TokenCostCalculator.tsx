// Token Cost Calculator — a client-side estimator for token counts and monthly
// spend. Everything runs in the browser; nothing is uploaded. Prices are typed
// in by hand because a gateway's rates come from its own model/pricing config,
// not from a public price list. Visual vocabulary (grid wash, pill eyebrow,
// numbered section intros, divided FAQ, ClosingCta) matches the Open Source
// page so the public pages read as one system.

import { useId, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Calculator, RotateCcw } from "lucide-react";
import { NumberInput } from "@/components/ui";
import { ClosingCta } from "@/components/ClosingCta";

/** English text averages ~4 characters per token. This is a heuristic, not a
 *  tokenizer — the page says so wherever the number is shown. */
const CHARS_PER_TOKEN = 4;

/** Provider prompt caching typically bills a cached prefix at ~10% of input. */
const CACHE_RATE = 0.1;

const DEFAULTS = {
  inputPrice: "2.50",
  outputPrice: "10.00",
  cachedShare: "0",
  outputTokens: "500",
  requests: "1000",
};

const LINK =
  "rounded text-[var(--admin-text)] underline decoration-white/20 underline-offset-4 hover:decoration-white/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/60";

function money(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n === 0) return "$0.00";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function count(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return Math.round(n).toLocaleString("en-US");
}

function num(v: string): number {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

const FAQ: { question: string; answer: string }[] = [
  {
    question: "How does this page count tokens?",
    answer:
      "It estimates them from the character count, assuming roughly four characters per English token. That is the rule of thumb providers themselves quote, and it is close enough for budgeting. It is not a tokenizer: code, JSON, and non-English text tokenize less efficiently, so for an exact count use the tokenizer your provider publishes for the model you are pricing.",
  },
  {
    question: "How is the cost calculated?",
    answer:
      "Providers quote a price per million tokens, separately for input (everything you send, including system prompt and history) and output (what the model generates). The estimate is (input tokens × input price + output tokens × output price) ÷ 1,000,000, multiplied by the number of requests per month. If you set a cached share, that part of the input is billed at a tenth of the input rate.",
  },
  {
    question: "Why is output more expensive than input?",
    answer:
      "Generating a token takes more compute than reading one, so output rates are typically two to four times input rates. That is why a workload that reads a long document and writes a short summary costs very differently from one that writes long code blocks from a short prompt.",
  },
  {
    question: "Why does my real bill differ from the estimate?",
    answer:
      "Four things move it: output length is a guess until the model answers, reasoning models bill hidden thinking tokens as output, cache discounts vary by provider and expire, and negotiated or volume rates may differ from list price. The estimate is a planning number, not an invoice.",
  },
  {
    question: "Does wiwi add a markup or platform fee?",
    answer:
      "No. wiwi is self-hosted software, not a reseller: requests go from your deployment to the provider account you configured, so you pay the provider's rate directly. There is no per-token fee, no seat fee, and no billing layer in the gateway.",
  },
  {
    question: "Where can I see what I actually spent?",
    answer:
      "The console's Usage and Analytics pages aggregate the gateway's own request logs, including per-key spend, token counts, and provider prompt-cache hits. Those numbers come from real traffic rather than a model, so they are the right place to reconcile against a provider invoice.",
  },
];

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

/** Wrapping in <label> associates the text with the input inside NumberInput
 *  without needing an id prop on it. */
function Field(props: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="admin-label mb-2 flex items-baseline justify-between gap-2">
        {props.label}
        {props.hint && (
          <span className="font-mono text-[10px] normal-case tracking-normal text-[var(--admin-text-dim)]">{props.hint}</span>
        )}
      </span>
      {props.children}
    </label>
  );
}

function Stat(props: { label: string; value: string; sub?: string; primary?: boolean }) {
  return (
    <div className="min-w-0 bg-[var(--admin-surface)] px-5 py-5">
      <p className="admin-label">{props.label}</p>
      <p
        className={`mt-1.5 break-all font-mono font-semibold tabular-nums ${
          props.primary ? "text-[28px] text-[var(--admin-text)] sm:text-[30px]" : "text-[22px] text-[var(--admin-text)]"
        }`}
      >
        {props.value}
      </p>
      {props.sub && <p className="mt-1 text-[12px] tabular-nums text-[var(--admin-text-dim)]">{props.sub}</p>}
    </div>
  );
}

export function TokenCostCalculatorPage() {
  const promptId = useId();
  const [prompt, setPrompt] = useState("");
  const [inputPrice, setInputPrice] = useState(DEFAULTS.inputPrice);
  const [outputPrice, setOutputPrice] = useState(DEFAULTS.outputPrice);
  const [cachedShare, setCachedShare] = useState(DEFAULTS.cachedShare);
  const [outputTokens, setOutputTokens] = useState(DEFAULTS.outputTokens);
  const [requests, setRequests] = useState(DEFAULTS.requests);

  const calc = useMemo(() => {
    const chars = prompt.length;
    const words = prompt.trim() ? prompt.trim().split(/\s+/).length : 0;
    const promptTokens = Math.ceil(chars / CHARS_PER_TOKEN);
    const outTokens = num(outputTokens);
    const reqs = num(requests);
    const inPrice = num(inputPrice);
    const outPrice = num(outputPrice);
    const cached = Math.min(num(cachedShare), 100) / 100;
    const effectiveInPrice = inPrice * (1 - cached + cached * CACHE_RATE);
    const inCost = (promptTokens * effectiveInPrice) / 1_000_000;
    const outCost = (outTokens * outPrice) / 1_000_000;
    const perRequest = inCost + outCost;
    const monthlyCost = perRequest * reqs;
    return {
      chars,
      words,
      promptTokens,
      outTokens,
      reqs,
      outPrice,
      cached,
      effectiveInPrice,
      perRequest,
      inShare: perRequest > 0 ? inCost / perRequest : 0,
      monthlyIn: promptTokens * reqs,
      monthlyOut: outTokens * reqs,
      monthlyCost,
      yearlyCost: monthlyCost * 12,
    };
  }, [prompt, inputPrice, outputPrice, cachedShare, outputTokens, requests]);

  const dirty =
    prompt !== "" ||
    inputPrice !== DEFAULTS.inputPrice ||
    outputPrice !== DEFAULTS.outputPrice ||
    cachedShare !== DEFAULTS.cachedShare ||
    outputTokens !== DEFAULTS.outputTokens ||
    requests !== DEFAULTS.requests;

  function reset() {
    setPrompt("");
    setInputPrice(DEFAULTS.inputPrice);
    setOutputPrice(DEFAULTS.outputPrice);
    setCachedShare(DEFAULTS.cachedShare);
    setOutputTokens(DEFAULTS.outputTokens);
    setRequests(DEFAULTS.requests);
  }

  const inPct = Math.round(calc.inShare * 100);

  return (
    <div className="mx-auto max-w-6xl space-y-20 pb-16 sm:space-y-24">
      {/* ── hero + calculator: the tool is the hero ── */}
      <section
        aria-labelledby="calc-title"
        className="relative overflow-hidden rounded-[24px] border border-[var(--admin-border)] bg-[var(--admin-surface)] sm:rounded-[32px]"
      >
        <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
          <div className="hero-grid absolute inset-x-0 top-0 h-[420px] opacity-60" />
        </div>
        <div className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-70" aria-hidden />

        <div className="relative px-6 pt-12 sm:px-10 sm:pt-14 lg:px-14">
          <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-blue-500/20 bg-blue-500/[0.06] px-3.5 py-1.5 text-blue-300">
            <Calculator size={12} aria-hidden />
            <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.2em]">Runs in your browser</span>
          </div>
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between lg:gap-12">
            <h1
              id="calc-title"
              className="text-3xl font-semibold leading-[1.08] tracking-[-0.025em] text-[var(--admin-text)] sm:text-4xl lg:text-[48px]"
            >
              LLM token <span className="hero-gradient-text">cost calculator</span>
            </h1>
            <p className="max-w-md text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
              Paste a prompt, enter your provider's per-million rates, and see the cost of one
              request and a month of them. Nothing is uploaded.
            </p>
          </div>
        </div>

        <div className="relative mt-10 grid gap-8 border-t border-[var(--admin-border)] px-6 py-8 sm:px-10 lg:grid-cols-[1.2fr_1fr] lg:gap-10 lg:px-14 lg:py-10">
          {/* prompt */}
          <div className="flex min-w-0 flex-col">
            <div className="mb-2 flex min-h-11 items-center justify-between gap-2">
              <label htmlFor={promptId} className="admin-label">
                Prompt or document
              </label>
              {dirty && (
                <button
                  type="button"
                  onClick={reset}
                  className="inline-flex min-h-11 items-center gap-1.5 rounded-lg px-2 text-[12px] text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/60"
                >
                  <RotateCcw size={12} aria-hidden />
                  Reset
                </button>
              )}
            </div>
            <textarea
              id={promptId}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Paste the system prompt, history, and user message you send to the model…"
              spellCheck={false}
              aria-describedby={`${promptId}-stats`}
              className="admin-input min-h-[220px] w-full flex-1 resize-y font-mono text-[12px] leading-relaxed lg:min-h-[300px]"
            />
            <p id={`${promptId}-stats`} className="mt-2 font-mono text-[11px] tabular-nums text-[var(--admin-text-dim)]">
              {calc.chars === 0
                ? "Nothing pasted yet — input cost is $0 until you do."
                : `≈ ${count(calc.promptTokens)} tokens (estimated) · ${count(calc.words)} words · ${count(calc.chars)} chars`}
            </p>
          </div>

          {/* rates + volume */}
          <div className="grid content-start gap-4 sm:grid-cols-2">
            <Field label="Input price" hint="$ / 1M">
              <NumberInput value={inputPrice} onChange={setInputPrice} min={0} step="any" suffix="$" />
            </Field>
            <Field label="Output price" hint="$ / 1M">
              <NumberInput value={outputPrice} onChange={setOutputPrice} min={0} step="any" suffix="$" />
            </Field>
            <Field label="Response size" hint="per request">
              <NumberInput value={outputTokens} onChange={setOutputTokens} min={0} step={100} suffix="tokens" />
            </Field>
            <Field label="Requests" hint="per month">
              <NumberInput value={requests} onChange={setRequests} min={0} step={100} suffix="reqs" />
            </Field>
            <div className="sm:col-span-2">
              <Field label="Cached input" hint="billed at 10% of input">
                <NumberInput value={cachedShare} onChange={setCachedShare} min={0} step={10} suffix="%" />
              </Field>
            </div>
          </div>
        </div>

        {/* results */}
        <div aria-live="polite" className="relative border-t border-[var(--admin-border)]">
          <div className="grid gap-px bg-[var(--admin-border)] sm:grid-cols-2 lg:grid-cols-4">
            <Stat primary label="Monthly cost" value={money(calc.monthlyCost)} sub={`${money(calc.yearlyCost)} / year`} />
            <Stat label="Per request" value={money(calc.perRequest)} sub={`× ${count(calc.reqs)} requests`} />
            <Stat
              label="Monthly tokens"
              value={count(calc.monthlyIn + calc.monthlyOut)}
              sub={`${count(calc.monthlyIn)} in · ${count(calc.monthlyOut)} out`}
            />
            <div className="min-w-0 bg-[var(--admin-surface)] px-5 py-5">
              <p className="admin-label">Cost split</p>
              {calc.perRequest > 0 ? (
                <>
                  <div
                    className="mt-3 flex h-2 overflow-hidden rounded-full bg-white/[0.04]"
                    role="img"
                    aria-label={`Input ${inPct}% of cost, output ${100 - inPct}%`}
                  >
                    <span className="bg-blue-400/80 transition-[width] duration-300 motion-reduce:transition-none" style={{ width: `${inPct}%` }} />
                    <span className="bg-violet-400/70 transition-[width] duration-300 motion-reduce:transition-none" style={{ width: `${100 - inPct}%` }} />
                  </div>
                  <p className="mt-2.5 flex justify-between font-mono text-[11px] tabular-nums text-[var(--admin-text-dim)]">
                    <span>
                      <span className="text-blue-300" aria-hidden>■</span> in {inPct}%
                    </span>
                    <span>
                      out {100 - inPct}% <span className="text-violet-300" aria-hidden>■</span>
                    </span>
                  </p>
                </>
              ) : (
                <p className="mt-2 text-[12px] text-[var(--admin-text-dim)]">Paste a prompt or set a response size to see the split.</p>
              )}
            </div>
          </div>
          <p className="overflow-x-auto whitespace-nowrap border-t border-[var(--admin-border)] bg-[#070707]/60 px-6 py-3 font-mono text-[11px] tabular-nums text-[var(--admin-text-muted)] sm:px-10 lg:px-14">
            ({count(calc.promptTokens)} in × ${calc.effectiveInPrice.toFixed(calc.cached ? 3 : 2)} +{" "}
            {count(calc.outTokens)} out × ${calc.outPrice.toFixed(2)}) ÷ 1,000,000 ={" "}
            <span className="text-[var(--admin-text)]">{money(calc.perRequest)}</span> per request
          </p>
        </div>
      </section>

      {/* ── explainer ── */}
      <section className="scroll-reveal">
        <SectionIntro
          index=".01 · the arithmetic"
          title="Understanding LLM token costs"
          body="Three inputs and one multiplication — the same arithmetic a provider uses on its invoice."
        />
        <div className="grid gap-x-10 gap-y-5 text-[14px] leading-relaxed text-[var(--admin-text-muted)] md:grid-cols-2">
          <p>
            Every model bills by the token, the small chunks of text it reads and writes. English
            averages about four characters per token, so 1,000 tokens is roughly 750 words. Code,
            JSON, and non-English text tokenize less efficiently — if a bill is off by more than a
            rounding error, the tokenizer is usually why.
          </p>
          <p>
            Input and output are priced separately, and output usually costs two to four times
            more. The split matters more than the total: summarizing a long document is cheap per
            request, while code generation that writes thousands of output tokens is not, even with
            a short prompt.
          </p>
          <p>
            Prompt caching discounts a repeated prefix — often to about a tenth of the input rate —
            which is why agentic sessions with a stable system prompt get cheaper per turn.
            Reasoning models bill hidden thinking tokens as output, so their effective output price
            is higher than the headline rate.
          </p>
          <p>
            wiwi does not sit between you and the provider's bill: it runs against your own provider
            accounts, so the rate you enter here is the rate you pay. For measured spend instead of an
            estimate, open{" "}
            <Link to="/console/usage" className={LINK}>
              Usage
            </Link>{" "}
            in the console, which aggregates the gateway's own request logs.
          </p>
        </div>
      </section>

      {/* ── FAQ ── */}
      <section className="scroll-reveal grid gap-8 lg:grid-cols-[1fr_2fr] lg:gap-14">
        <div className="lg:sticky lg:top-24 lg:self-start">
          <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-blue-300/80">.02 · faq</span>
          <h2 className="mt-2 text-[24px] font-semibold tracking-[-0.015em] text-[var(--admin-text)] sm:text-[28px]">
            Estimates vs invoices
          </h2>
          <p className="mt-3 text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            What the estimate does, what it leaves out, and where to check the real bill.
          </p>
        </div>
        <div className="divide-y divide-[var(--admin-border)] rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)]">
          {FAQ.map((item) => (
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
        eyebrow=".03 · measure it for real"
        title="Swap the estimate for live numbers."
        body="Run the gateway against your own provider keys and every request is logged with its tokens, cache hits, and cost — at the provider's rate, with no markup."
        primary={{ label: "Self-host the gateway", to: "/open-source" }}
        secondary={{ label: "Read the docs", to: "/docs" }}
        footnote="MIT-licensed. No per-token fee, no seat fee."
        commands={[
          { title: "Prepare the config", command: "cp wiwi.yaml.example wiwi.yaml" },
          { title: "Start the stack", command: "docker compose up --build" },
        ]}
      />
    </div>
  );
}
