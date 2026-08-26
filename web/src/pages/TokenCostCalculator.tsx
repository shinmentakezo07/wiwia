// Token Cost Calculator — LLM token counter and cost calculator. Adapted from
// the llmgateway.io page with inlined FAQ data, in the dark design system.

import { Link } from "react-router-dom";
import { ChevronDown, FileText, Route, SlidersHorizontal } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const STEPS: { icon: LucideIcon; title: string; description: string }[] = [
  {
    icon: FileText,
    title: "Paste your prompt or document",
    description:
      "Drop in real text, code, or a JSON payload. A BPE tokenizer runs in your browser to count the exact tokens — the same way the model bills you — with nothing uploaded.",
  },
  {
    icon: SlidersHorizontal,
    title: "Set your output size and volume",
    description:
      "Choose how long a response you expect and how many requests you send. Or switch to Estimate mode to enter input and output token volumes directly across multiple models.",
  },
  {
    icon: Route,
    title: "Compare every model and save",
    description:
      "See your prompt ranked across GPT-5, Claude, Gemini, and 200+ models at each provider's cheapest live rate — then route through the gateway to pay it automatically with zero markup.",
  },
];

const FAQ: { question: string; answer: string }[] = [
  {
    question: "How do I count the tokens in my prompt?",
    answer:
      "Paste your text into the calculator and it counts the exact tokens in your browser using a real BPE tokenizer (the GPT-4o / o200k_base encoding), the same kind of tokenizer the models use to bill you. Nothing is uploaded — the counting happens locally. You instantly see the token count alongside characters and words, plus what that text costs on every major model.",
  },
  {
    question: "How many tokens is 1,000 words or one page of text?",
    answer:
      "As a rule of thumb, 1,000 English words is roughly 1,300–1,500 tokens, and one token is about four characters, so 1,000 tokens is around 750 words. Code, JSON, and non-English text tokenize less efficiently and use more tokens per word, which is exactly why pasting your real text into the tokenizer gives a far more accurate count than a word-based estimate.",
  },
  {
    question: "How is the cost of LLM tokens calculated?",
    answer:
      "Providers bill separately for input tokens (your prompt) and output tokens (the model's response), priced per million tokens. Your total cost is (input tokens × input price) + (output tokens × output price). This calculator counts your input tokens exactly, lets you set an expected output length, and runs that math for every model.",
  },
  {
    question: "What is the difference between input and output tokens?",
    answer:
      "Input tokens are everything you send to the model, including your prompt, system message, and conversation history. Output tokens are what the model generates back. Output tokens almost always cost more than input tokens, which is why the split matters when you estimate spend.",
  },
  {
    question: "Why do the same model's prices differ between providers?",
    answer:
      "Popular models are often served by several providers at different rates, and prices change as providers compete. The gateway routes each request to the cheapest available provider for that model, so you pay the lowest live rate without changing any code.",
  },
  {
    question: "Does the gateway add a markup or platform fee?",
    answer:
      "No. The gateway passes through provider pricing with zero platform markup, so you pay exactly what the provider charges (and less when a cheaper provider or volume discount is available). You only add a payment method once you start sending real traffic.",
  },
  {
    question: "How accurate are these cost estimates?",
    answer:
      "Input token counts come from a real BPE tokenizer running on your exact text, so they closely match what providers measure. Costs use each model's current published per-token prices. The main variables are output length (you estimate it, since it isn't known until the model responds), prompt caching, reasoning tokens on thinking models, and any negotiated rates. Treat the numbers as a tight planning estimate rather than a final invoice.",
  },
  {
    question: "Do different models count tokens differently?",
    answer:
      "Yes. Each model family has its own tokenizer, so the same text can produce slightly different counts. This tool standardizes on the GPT-4o (o200k_base) tokenizer, which is the modern OpenAI standard and lands within roughly ±15% of other families like Claude, Gemini, and Llama — close enough for accurate budgeting, since none of those providers ship a tokenizer that runs in the browser.",
  },
  {
    question: "What is the cheapest way to call LLMs like GPT-4o, Claude, and Gemini?",
    answer:
      "Route through a gateway that compares providers and picks the lowest price per request. Because the gateway supports 200+ models behind one OpenAI-compatible API, you can switch models or providers based on cost without rewriting your integration.",
  },
  {
    question: "Is the token cost calculator free to use?",
    answer:
      "Yes, the calculator is completely free and requires no signup. You can compare as many models and token volumes as you like, then create a free gateway account when you are ready to start sending requests.",
  },
];

export function TokenCostCalculatorPage() {
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
          Free LLM token counter and cost calculator. Count tokens with a real tokenizer,
          then compare costs on GPT-5, Claude, Gemini, and 200+ models.
        </p>
      </section>

      {/* ── calculator placeholder ── */}
      <section>
        <Card className="p-8 text-center">
          <FileText className="mx-auto h-10 w-10 text-blue-400" />
          <h2 className="mt-4 text-[18px] font-semibold text-[var(--admin-text)]">
            Interactive tokenizer & calculator
          </h2>
          <p className="mt-2 text-[14px] text-[var(--admin-text-muted)]">
            Paste a prompt to count tokens, then compare cost across GPT-5, Claude,
            Gemini, and 200+ models at zero markup.
          </p>
          <Link
            to="/signup"
            className="mt-5 inline-flex items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 py-3 text-[14px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] hover:brightness-110"
          >
            Start free
          </Link>
        </Card>
      </section>

      {/* ── how it works ── */}
      <section>
        <div className="mb-6 text-center">
          <h2 className="text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            How the LLM cost calculator works
          </h2>
          <p className="mx-auto mt-2 max-w-2xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            Count the exact tokens in your prompt and price it across every major model
            in three steps, then see how much routing through the gateway saves you.
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
                  <span className="font-mono text-[13px] font-semibold text-[var(--admin-text-muted)]" style={{ fontFamily: MONO }}>
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
            Every large language model bills by the token, the small chunks of text a
            model reads and writes. Roughly speaking, one token is about four characters
            of English, so 1,000 tokens is around 750 words — but the only accurate way
            to know is to tokenize the exact text. This calculator does that in your
            browser with a real BPE tokenizer, so the token counts match how the model
            actually bills you instead of a rough character estimate. Providers quote
            prices per million tokens, and they charge separately for the tokens you
            send (input) and the tokens the model generates (output).
          </p>
          <p>
            Output tokens are usually two to four times more expensive than input tokens,
            so the ratio between your prompt size and response size has a big impact on
            your bill. A summarization workload that reads a lot and writes a little
            costs very differently from a code-generation workload that writes long
            responses. The calculator keeps the two separate so your estimate reflects
            how you actually use each model.
          </p>
          <p>
            Prices also vary by provider. A single popular model is often hosted by
            several providers at different rates, and those rates change as providers
            compete on price. Instead of locking yourself into one provider, the gateway
            routes each request to the cheapest available provider for that model
            through one OpenAI-compatible API, with no platform markup. That is the gap
            the calculator shows: the official list price versus the lowest live price
            you would actually pay.
          </p>
          <p>
            Use it to budget a new feature, compare GPT-4o against Claude or Gemini
            before you commit, or build the business case for switching providers. When
            the numbers look good, you can{" "}
            <Link to="/signup" className="font-medium text-blue-400 underline-offset-4 hover:underline">
              start for free
            </Link>{" "}
            and keep the same estimate in production.
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
            Everything you need to know about estimating and lowering your LLM token
            costs.
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
    </div>
  );
}
