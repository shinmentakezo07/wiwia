// Hero — landing hero section with announcement badge, headline, CTA, trust
// indicators, and a provider logo grid. Converted from the llmgateway reference:
// next/link → react-router-dom Link, next/image → <img>, framer-motion → CSS
// animation classes (wiwi-enter), ShimmerButton → inline shimmer-styled Link,
// @llmgateway/shared providerLogoUrls → inline provider name grid.

import { ArrowRight, ChevronRight } from "lucide-react";
import { Link } from "react-router-dom";
import { GithubStars } from "./GithubStars";

const PROVIDERS = [
  "OpenAI",
  "Anthropic",
  "Together AI",
  "Groq",
  "xAI",
  "DeepSeek",
  "Perplexity",
  "Google",
  "Mistral",
  "Cerebras",
  "Fireworks",
  "AWS Bedrock",
  "Azure",
  "Alibaba",
  "Nebius",
  "Novita",
];

const MIGRATIONS = [
  { slug: "openai", title: "Migrate from OpenAI", fromProvider: "OpenAI" },
  { slug: "openrouter", title: "Migrate from OpenRouter", fromProvider: "OpenRouter" },
  { slug: "litellm", title: "Migrate from LiteLLM", fromProvider: "LiteLLM" },
];

const providerIcons: Record<string, React.ReactNode> = {
  OpenRouter: (
    <svg
      fill="currentColor"
      fillRule="evenodd"
      viewBox="0 0 24 24"
      xmlns="http://www.w3.org/2000/svg"
      className="size-5"
      aria-hidden="true"
    >
      <path d="m16.804 1.957 7.22 4.105v.087L16.73 10.21l.017-2.117-.821-.03c-1.059-.028-1.611.002-2.268.11-1.064.175-2.038.577-3.147 1.352L8.345 11.03c-.284.195-.495.336-.68.455l-.515.322-.397.234.385.23.53.338c.476.314 1.17.796 2.701 1.866 1.11.775 2.083 1.177 3.147 1.352l.3.045c.694.091 1.375.094 2.825.033l.022-2.159 7.22 4.105v.087L16.589 22l.014-1.862-.635.022c-1.386.042-2.137.002-3.138-.162-1.694-.28-3.26-.926-4.881-2.059l-2.158-1.5a21.997 21.997 0 0 0-.755-.498l-.467-.28a55.927 55.927 0 0 0-.76-.43C2.908 14.73.563 14.116 0 14.116V9.888l.14.004c.564-.007 2.91-.622 3.809-1.124l1.016-.58.438-.274c.428-.28 1.072-.726 2.686-1.853 1.621-1.133 3.186-1.78 4.881-2.059 1.152-.19 1.974-.213 3.814-.138z" />
    </svg>
  ),
  LiteLLM: (
    <span className="text-lg" role="img" aria-label="LiteLLM">
      🚅
    </span>
  ),
  OpenAI: <span className="text-xs font-bold">OAI</span>,
};

export function Hero() {
  return (
    <main className="overflow-hidden">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 z-2 hidden opacity-50 mix-blend-screen lg:block"
      >
        <div className="absolute left-0 top-0 -rotate-45 -translate-y-[350px] w-140 h-320 rounded-full bg-[radial-gradient(68.54%_68.72%_at_55.02%_31.46%,hsla(0,0%,85%,.08)_0,hsla(0,0%,55%,.02)_50%,hsla(0,0%,45%,0)_80%)]" />
        <div className="absolute left-0 top-0 w-56 h-320 -rotate-45 rounded-full bg-[radial-gradient(50%_50%_at_50%_50%,hsla(0,0%,85%,.06)_0,hsla(0,0%,45%,.02)_80%,transparent_100%)] [translate:5%_-50%]" />
      </div>
      <section>
        <div className="relative pt-36">
          <div
            aria-hidden
            className="absolute inset-0 -z-10 size-full [background:radial-gradient(125%_125%_at_50%_100%,transparent_0%,var(--admin-bg)_75%)]"
          />
          <div className="mx-auto max-w-7xl px-6">
            {/* Announcement badge */}
            <div className="mb-10 flex justify-center lg:mb-12">
              <div className="wiwi-enter">
                <Link
                  to="/docs"
                  className="group flex w-fit items-center gap-4 rounded-full border border-[var(--admin-border)] bg-white/[0.03] p-1 pl-4 shadow-md shadow-black/5 transition-all duration-300 hover:bg-white/[0.05]"
                >
                  <span className="text-sm text-[var(--admin-text)]">
                    wiwi is now open source
                  </span>
                  <span className="block h-4 w-0.5 bg-white/20" />
                  <div className="size-6 overflow-hidden rounded-full">
                    <div className="flex w-12 -translate-x-1/2 transition-transform duration-500 ease-in-out group-hover:translate-x-0">
                      <span className="flex size-6">
                        <ArrowRight className="m-auto size-3" />
                      </span>
                      <span className="flex size-6">
                        <ArrowRight className="m-auto size-3" />
                      </span>
                    </div>
                  </div>
                </Link>
              </div>
            </div>

            {/* Centered hero content */}
            <div className="mx-auto max-w-4xl text-center">
              <div className="wiwi-enter">
                <h1 className="text-balance text-4xl font-bold tracking-tight text-[var(--admin-text)] md:text-5xl lg:text-6xl">
                  wiwi — One API for 40+ providers, including OpenAI, Anthropic, and Google
                </h1>
                <p className="mx-auto mt-4 max-w-2xl text-balance text-base text-[var(--admin-text-muted)] md:text-lg">
                  Stop juggling API keys and provider dashboards. Route requests across 200+
                  models, track costs in real-time, and switch providers without changing your
                  code.
                </p>
              </div>

              {/* Primary CTA */}
              <div className="mt-8 flex flex-col items-center gap-6 md:mt-10">
                <div className="relative">
                  {/* Outer glow ring */}
                  <div className="absolute -inset-3 rounded-full bg-blue-500/30 blur-xl" />
                  <Link
                    to="/signup"
                    className="wiwi-shimmer group relative inline-flex items-center gap-3 rounded-full bg-blue-600 px-10 py-3 text-xl font-bold tracking-tight text-white shadow-2xl shadow-blue-500/25 transition-colors hover:bg-blue-500 md:px-12 md:py-4"
                  >
                    <span className="flex items-center gap-3 text-center leading-none">
                      <span>Get My API Key</span>
                      <ArrowRight className="size-6 transition-transform group-hover:translate-x-1 md:size-7" />
                    </span>
                  </Link>
                </div>

                {/* Trust indicators */}
                <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-sm text-[var(--admin-text-muted)]">
                  {["Bring your own keys — free forever", "No credit card required", "Setup in 30 seconds"].map(
                    (text) => (
                      <span key={text} className="flex items-center gap-1.5">
                        <svg className="size-4 text-green-500" fill="currentColor" viewBox="0 0 20 20" aria-hidden="true">
                          <path
                            fillRule="evenodd"
                            d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                            clipRule="evenodd"
                          />
                        </svg>
                        {text}
                      </span>
                    ),
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Migration guides */}
          <div className="mx-auto mt-10 max-w-4xl px-6">
            <p className="mb-4 text-center text-sm text-[var(--admin-text-muted)]">
              Switching from another provider?
            </p>
            <div className="flex flex-wrap items-center justify-center gap-3">
              {MIGRATIONS.map((migration) => (
                <Link
                  key={migration.slug}
                  to={`/docs`}
                  className="group/card flex items-center gap-2 rounded-full border border-[var(--admin-border)] bg-[var(--admin-surface)] px-4 py-2 text-sm transition-colors hover:border-blue-500/50 hover:bg-white/[0.04]"
                >
                  <span className="flex size-6 items-center justify-center text-[var(--admin-text-muted)] transition-colors group-hover/card:text-[var(--admin-text)]">
                    {providerIcons[migration.fromProvider] ?? (
                      <ChevronRight className="size-4" aria-hidden="true" />
                    )}
                  </span>
                  <span className="text-[var(--admin-text-muted)] transition-colors group-hover/card:text-[var(--admin-text)]">
                    {migration.fromProvider}
                  </span>
                  <ArrowRight
                    className="size-3 text-[var(--admin-text-muted)] transition-transform group-hover/card:translate-x-0.5 group-hover/card:text-blue-400"
                    aria-hidden="true"
                  />
                </Link>
              ))}
              <Link
                to="/docs"
                className="flex items-center gap-1 rounded-full px-3 py-2 text-sm text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]"
              >
                <span>View all</span>
                <ChevronRight className="size-3" aria-hidden="true" />
              </Link>
            </div>
          </div>

          {/* Dashboard preview */}
          <div className="relative -mr-56 mt-8 overflow-hidden px-2 sm:mr-0 sm:mt-12 md:mt-20">
            <div
              aria-hidden
              className="absolute inset-0 z-10 bg-linear-to-b from-transparent to-[var(--admin-bg)]"
            />
            <div className="relative mx-auto max-w-6xl overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-4 shadow-lg shadow-black/15 ring-1 ring-white/[0.02]">
              <div className="relative aspect-[3022/1650] overflow-hidden rounded-2xl">
                {/* Faux dashboard preview (no screenshot asset needed) */}
                <div className="absolute inset-0 bg-gradient-to-br from-blue-500/5 via-transparent to-purple-500/5" />
                <div className="flex h-full flex-col p-8">
                  <div className="mb-4 flex items-center gap-2">
                    <div className="size-2.5 rounded-full bg-red-400/60" />
                    <div className="size-2.5 rounded-full bg-yellow-400/60" />
                    <div className="size-2.5 rounded-full bg-green-400/60" />
                  </div>
                  <div className="grid flex-1 grid-cols-3 gap-4">
                    <div className="rounded-lg border border-[var(--admin-border)] bg-white/[0.02] p-4">
                      <div className="mb-2 h-2 w-16 rounded bg-white/10" />
                      <div className="h-6 w-24 rounded bg-white/5" />
                    </div>
                    <div className="rounded-lg border border-[var(--admin-border)] bg-white/[0.02] p-4">
                      <div className="mb-2 h-2 w-16 rounded bg-white/10" />
                      <div className="h-6 w-20 rounded bg-white/5" />
                    </div>
                    <div className="rounded-lg border border-[var(--admin-border)] bg-white/[0.02] p-4">
                      <div className="mb-2 h-2 w-16 rounded bg-white/10" />
                      <div className="h-6 w-28 rounded bg-white/5" />
                    </div>
                    <div className="col-span-2 rounded-lg border border-[var(--admin-border)] bg-white/[0.02] p-4">
                      <div className="mb-3 h-2 w-20 rounded bg-white/10" />
                      <div className="flex h-32 items-end gap-2">
                        {[40, 65, 50, 80, 55, 90, 70, 60, 85, 45, 75, 95].map((h, i) => (
                          <div
                            key={i}
                            className="flex-1 rounded-t bg-gradient-to-t from-blue-500/30 to-blue-500/60"
                            style={{ height: `${h}%` }}
                          />
                        ))}
                      </div>
                    </div>
                    <div className="rounded-lg border border-[var(--admin-border)] bg-white/[0.02] p-4">
                      <div className="mb-2 h-2 w-16 rounded bg-white/10" />
                      <div className="space-y-2">
                        {[60, 80, 40, 50].map((w, i) => (
                          <div key={i} className="h-2 rounded bg-white/5" style={{ width: `${w}%` }} />
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Provider logos grid */}
      <section className="bg-[var(--admin-bg)] pt-16 pb-16 md:pb-32">
        <div className="group relative m-auto max-w-5xl px-6">
          <div className="absolute inset-0 z-10 flex scale-95 items-center justify-center opacity-0 duration-500 group-hover:scale-100 group-hover:opacity-100">
            <Link to="/models" className="block text-sm duration-150 hover:opacity-75">
              <span>View All Providers</span>
              <ChevronRight className="ml-1 inline-block size-3" />
            </Link>
          </div>
          <div className="mx-auto mt-12 grid max-w-3xl grid-cols-5 gap-x-10 gap-y-6 transition-all duration-500 group-hover:opacity-50 sm:grid-cols-6 sm:gap-x-12 sm:gap-y-10 lg:grid-cols-8">
            {PROVIDERS.map((provider) => (
              <div key={provider} className="flex">
                <span className="mx-auto text-sm font-medium text-[var(--admin-text-muted)]">
                  {provider}
                </span>
              </div>
            ))}
          </div>
        </div>
      </section>
    </main>
  );
}

export { GithubStars };
