// Token Cost Calculator — a client-side estimator for token counts and monthly
// spend. Everything runs in the browser; nothing is uploaded. Prices are typed
// in by hand because a gateway's rates come from its own model/pricing config,
// not from a public price list.

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, FileText, Gauge, SlidersHorizontal } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card, NumberInput } from "@/components/ui";

/** English text averages ~4 characters per token. This is a heuristic, not a
 *  tokenizer — the page says so wherever the number is shown. */
const CHARS_PER_TOKEN = 4;

function money(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n === 0) return "$0.00";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
}

function count(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return Math.round(n).toLocaleString("en-US");
}

function num(v: string): number {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

const STEPS: { icon: LucideIcon; title: string; description: string }[] = [
  {
    icon: FileText,
    title: "Paste the prompt",
    description:
      "Drop in real text, code, or a JSON payload. The page estimates the token count from its length — no upload, no network call.",
  },
  {
    icon: SlidersHorizontal,
    title: "Enter the prices",
    description:
      "Copy the input and output price per million tokens from your provider's price list, then set the response size and monthly volume.",
  },
  {
    icon: Gauge,
    title: "Read the bill",
    description:
      "See the cost of one request and the projected monthly spend, split into input and output tokens so the ratio is visible.",
  },
];

const FAQ: { question: string; answer: string }[] = [
  {
    question: "How does this page count tokens?",
    answer:
      "It estimates them from the character count, assuming roughly four characters per English token. That is the rule of thumb providers themselves quote, and it is close enough for budgeting. It is not a tokenizer: code, JSON, and non-English text tokenize less efficiently, so for an exact count use the tokenizer your provider publishes for the model you are pricing.",
  },
  {
    question: "How is the cost calculated?",
    answer:
      "Providers quote a price per million tokens, separately for input (everything you send, including system prompt and history) and output (what the model generates). The estimate is (input tokens × input price + output tokens × output price) ÷ 1,000,000, multiplied by the number of requests per month.",
  },
  {
    question: "Why is output more expensive than input?",
    answer:
      "Generating a token takes more compute than reading one, so output rates are typically two to four times input rates. That is why a workload that reads a long document and writes a short summary costs very differently from one that writes long code blocks from a short prompt.",
  },
  {
    question: "Why does my real bill differ from the estimate?",
    answer:
      "Four things move it: output length is a guess until the model answers, reasoning models bill hidden thinking tokens as output, provider prompt caching discounts repeated prefixes (often to about a tenth of the input rate), and negotiated or volume rates may differ from list price. The estimate is a planning number, not an invoice.",
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

export function TokenCostCalculatorPage() {
  const [prompt, setPrompt] = useState("");
  const [inputPrice, setInputPrice] = useState("2.50");
  const [outputPrice, setOutputPrice] = useState("10.00");
  const [outputTokens, setOutputTokens] = useState("500");
  const [requests, setRequests] = useState("1000");

  const calc = useMemo(() => {
    const chars = prompt.length;
    const words = prompt.trim() ? prompt.trim().split(/\s+/).length : 0;
    const promptTokens = Math.ceil(chars / CHARS_PER_TOKEN);
    const outTokens = num(outputTokens);
    const reqs = num(requests);
    const inPrice = num(inputPrice);
    const outPrice = num(outputPrice);
    const perRequest = (promptTokens * inPrice + outTokens * outPrice) / 1_000_000;
    const monthlyIn = promptTokens * reqs;
    const monthlyOut = outTokens * reqs;
    return {
      chars,
      words,
      promptTokens,
      perRequest,
      monthlyIn,
      monthlyOut,
      monthlyCost: perRequest * reqs,
      reqs,
      inPrice,
      outPrice,
    };
  }, [prompt, inputPrice, outputPrice, outputTokens, requests]);

  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="text-center">
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          LLM Token Cost{" "}
          <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
            Calculator
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          Estimate the tokens in a prompt and what it costs per request and per month, at
          any provider's published rates. The math runs in your browser.
        </p>
      </section>

      {/* ── calculator ── */}
      <section>
        <Card className="p-5 sm:p-6">
          <div className="grid gap-6 md:grid-cols-2">
            <div>
              <label
                htmlFor="prompt"
                className="admin-label mb-2 block"
              >
                Prompt or document
              </label>
              <textarea
                id="prompt"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Paste the text you send to the model…"
                className="admin-input min-h-[160px] w-full resize-y font-mono text-[12px] leading-relaxed"
              />
              <p className="mt-2 text-[12px] leading-relaxed text-[var(--admin-text-dim)]">
                {calc.chars === 0
                  ? "Nothing pasted yet."
                  : `${count(calc.promptTokens)} tokens (estimated) · ${count(calc.words)} words · ${count(calc.chars)} characters`}
              </p>
            </div>

            <div className="space-y-4">
              <div>
                <span className="admin-label mb-2 block">Input price</span>
                <NumberInput
                  value={inputPrice}
                  onChange={setInputPrice}
                  min={0}
                  step="any"
                  suffix="$/1M"
                />
              </div>
              <div>
                <span className="admin-label mb-2 block">Output price</span>
                <NumberInput
                  value={outputPrice}
                  onChange={setOutputPrice}
                  min={0}
                  step="any"
                  suffix="$/1M"
                />
              </div>
              <div>
                <span className="admin-label mb-2 block">Response size</span>
                <NumberInput
                  value={outputTokens}
                  onChange={setOutputTokens}
                  min={0}
                  step={100}
                  suffix="tokens"
                />
              </div>
              <div>
                <span className="admin-label mb-2 block">Requests per month</span>
                <NumberInput
                  value={requests}
                  onChange={setRequests}
                  min={0}
                  step={100}
                  suffix="reqs"
                />
              </div>
            </div>
          </div>

          <div className="mt-6 grid gap-3 border-t border-[var(--admin-border)] pt-5 sm:grid-cols-3">
            <div className="rounded-xl border border-[var(--admin-border)] bg-white/[0.02] p-4">
              <span className="admin-label block">Per request</span>
              <p className="mt-1.5 font-mono text-[22px] font-semibold tabular-nums text-[var(--admin-text)]">
                {money(calc.perRequest)}
              </p>
            </div>
            <div className="rounded-xl border border-[var(--admin-border)] bg-white/[0.02] p-4">
              <span className="admin-label block">Monthly tokens</span>
              <p className="mt-1.5 font-mono text-[22px] font-semibold tabular-nums text-[var(--admin-text)]">
                {count(calc.monthlyIn + calc.monthlyOut)}
              </p>
              <p className="mt-0.5 text-[11px] text-[var(--admin-text-dim)]">
                {count(calc.monthlyIn)} in · {count(calc.monthlyOut)} out
              </p>
            </div>
            <div className="rounded-xl border border-[var(--admin-border)] bg-white/[0.02] p-4">
              <span className="admin-label block">Monthly cost</span>
              <p className="mt-1.5 font-mono text-[22px] font-semibold tabular-nums text-[var(--admin-text)]">
                {money(calc.monthlyCost)}
              </p>
              <p className="mt-0.5 text-[11px] text-[var(--admin-text-dim)]">
                at {count(calc.reqs)} requests
              </p>
            </div>
          </div>

          <p className="mt-4 rounded-lg border border-[var(--admin-border)] bg-white/[0.02] px-3 py-2.5 font-mono text-[11px] leading-relaxed text-[var(--admin-text-muted)]">
            ({count(calc.promptTokens)} in × ${calc.inPrice.toFixed(2)} +{" "}
            {count(num(outputTokens))} out × ${calc.outPrice.toFixed(2)}) ÷ 1,000,000 ={" "}
            {money(calc.perRequest)} per request
          </p>
        </Card>
      </section>

      {/* ── how it works ── */}
      <section>
        <div className="mb-6 text-center">
          <h2 className="text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            How the estimate works
          </h2>
          <p className="mx-auto mt-2 max-w-2xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            Three inputs and one multiplication — the same arithmetic a provider uses on
            its invoice.
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          {STEPS.map((step, index) => {
            const Icon = step.icon;
            return (
              <Card key={step.title} className="p-5">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-500/10 text-blue-400">
                    <Icon className="h-5 w-5" />
                  </div>
                  <span className="font-mono text-[13px] font-semibold text-[var(--admin-text-muted)]">
                    Step {index + 1}
                  </span>
                </div>
                <h3 className="mt-4 text-[15px] font-semibold text-[var(--admin-text)]">{step.title}</h3>
                <p className="mt-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{step.description}</p>
              </Card>
            );
          })}
        </div>
      </section>

      {/* ── explainer ── */}
      <section>
        <h2 className="text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Understanding LLM token costs
        </h2>
        <div className="mt-5 space-y-4 text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          <p>
            Every model bills by the token, the small chunks of text it reads and writes.
            English averages about four characters per token, so 1,000 tokens is roughly
            750 words. Code, JSON, and non-English text tokenize less efficiently — if a
            bill is off by more than a rounding error, the tokenizer is usually why.
          </p>
          <p>
            Input and output are priced separately, and output usually costs two to four
            times more. The split matters more than the total: a summarization job that
            reads a long document and writes a paragraph is cheap per request, while code
            generation that writes thousands of output tokens is not, even with a short
            prompt.
          </p>
          <p>
            Two provider-side effects move the real number. Prompt caching discounts a
            repeated prefix — often to about a tenth of the input rate — which is why
            agentic sessions with a stable system prompt and repo context get cheaper per
            turn as they run. Reasoning models bill hidden thinking tokens as output, so
            their effective output price is higher than the headline rate.
          </p>
          <p>
            wiwi does not sit between you and the provider's bill: it is software you run
            against your own provider accounts, so the rate you enter here is the rate you
            pay. To see measured spend instead of an estimate, open the{" "}
            <Link to="/console/usage" className="font-medium text-blue-400 underline-offset-4 hover:underline">
              Usage
            </Link>{" "}
            page in the console, which aggregates the gateway's own request logs.
          </p>
        </div>
      </section>

      {/* ── FAQ ── */}
      <section>
        <div className="mb-6 text-center">
          <h2 className="text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Frequently asked questions
          </h2>
          <p className="mx-auto mt-2 max-w-2xl text-[14px] text-[var(--admin-text-muted)]">
            What the estimate does, what it leaves out, and where to check the real bill.
          </p>
        </div>
        <div className="space-y-3">
          {FAQ.map((item) => (
            <details
              key={item.question}
              className="group rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] [&_summary::-webkit-details-marker]:hidden"
            >
              <summary className="flex cursor-pointer items-center justify-between gap-4 px-5 py-4 text-left text-[14px] font-medium text-[var(--admin-text)]">
                {item.question}
                <ChevronDown className="h-5 w-5 shrink-0 text-[var(--admin-text-dim)] transition-transform duration-200 group-open:rotate-180" />
              </summary>
              <p className="px-5 pb-5 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                {item.answer}
              </p>
            </details>
          ))}
        </div>
      </section>

      {/* ── footer CTA ── */}
      <section className="text-center">
        <p className="text-[14px] text-[var(--admin-text-muted)]">
          Want the same math running against live traffic?{" "}
          <Link to="/docs" className="font-medium text-blue-400 underline-offset-4 hover:underline">
            Read the docs
          </Link>{" "}
          or{" "}
          <Link to="/open-source" className="font-medium text-blue-400 underline-offset-4 hover:underline">
            self-host the gateway
          </Link>
          .
        </p>
      </section>
    </div>
  );
}
