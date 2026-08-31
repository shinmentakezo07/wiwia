// Footer — full footer with newsletter, link columns, social icons, and status
// badge. Converted from the llmgateway reference: next/link → react-router-dom
// Link, radix DiscordLogoIcon → inline SVG, lucide GithubIcon stays, useAppConfig
// config → hardcoded URLs, listedProviders → inline array.

import { Link } from "react-router-dom";
import { Newsletter } from "./Newsletter";

const CONFIG = {
  githubUrl: "https://github.com/shinmentakezo07/wiwia",
  twitterUrl: "https://x.com",
  discordUrl: "https://discord.com",
};

const listedProviders = [
  { id: "openai", name: "OpenAI" },
  { id: "anthropic", name: "Anthropic" },
  { id: "google", name: "Google" },
  { id: "together-ai", name: "Together AI" },
  { id: "groq", name: "Groq" },
  { id: "xai", name: "xAI" },
  { id: "deepseek", name: "DeepSeek" },
  { id: "mistral", name: "Mistral" },
  { id: "fireworks", name: "Fireworks" },
  { id: "cerebras", name: "Cerebras" },
  { id: "aws-bedrock", name: "AWS Bedrock" },
  { id: "azure", name: "Azure" },
];

function DiscordIcon() {
  return (
    <svg className="size-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994a.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z" />
    </svg>
  );
}

export function Footer() {
  return (
    <footer className="relative bg-[var(--admin-bg)] py-12">
      {/* Gradient separator */}
      <div className="absolute left-0 right-0 top-0 h-px bg-gradient-to-r from-transparent via-[var(--admin-border)] to-transparent" />

      <div className="container mx-auto px-4">
        <Newsletter />

        <div className="flex flex-col md:flex-row md:items-start md:justify-between">
          {/* Left: brand + socials + status */}
          <div className="mb-8 md:mb-0 md:w-48 md:shrink-0">
            <div className="flex items-center gap-2">
              <a
                href={CONFIG.githubUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="flex size-9 items-center justify-center rounded-full border border-[var(--admin-border)] bg-white/[0.02] text-[var(--admin-text-muted)] transition-colors hover:border-white/10 hover:text-[var(--admin-text)]"
                aria-label="GitHub"
              >
                <svg className="size-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                  <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
                </svg>
              </a>
              <a
                href={CONFIG.twitterUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="flex size-9 items-center justify-center rounded-full border border-[var(--admin-border)] bg-white/[0.02] text-[var(--admin-text-muted)] transition-colors hover:border-white/10 hover:text-[var(--admin-text)]"
                aria-label="X"
              >
                <svg className="size-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                  <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
                </svg>
              </a>
              <a
                href={CONFIG.discordUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="flex size-9 items-center justify-center rounded-full border border-[var(--admin-border)] bg-white/[0.02] text-[var(--admin-text-muted)] transition-colors hover:border-white/10 hover:text-[var(--admin-text)]"
                aria-label="Discord"
              >
                <DiscordIcon />
              </a>
            </div>
            <a
              href="https://status.example.com/"
              target="_blank"
              rel="noopener noreferrer"
              className="mt-6 inline-flex items-center gap-2 rounded-full border border-[var(--admin-border)] bg-white/[0.02] px-3 py-1.5 text-xs text-[var(--admin-text-muted)] transition-colors hover:border-white/10 hover:text-[var(--admin-text)]"
            >
              <span className="relative flex size-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-75" />
                <span className="relative inline-flex size-2 rounded-full bg-green-500" />
              </span>
              All systems operational
            </a>
          </div>

          {/* Right: link columns */}
          <div className="grid w-full grid-cols-2 gap-8 text-[var(--admin-text-muted)] md:w-auto md:grid-cols-4 lg:grid-cols-6">
            {/* Product */}
            <div>
              <h3 className="mb-4 text-sm font-semibold text-[var(--admin-text)]">Product</h3>
              <ul className="space-y-2">
                <li><Link to="/" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Features</Link></li>
                <li><Link to="/models" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Models</Link></li>
                <li><Link to="/playground" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Playground</Link></li>
                <li><Link to="/pricing" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Pricing</Link></li>
                <li><Link to="/compare" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Compare</Link></li>
                <li><Link to="/enterprise" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Enterprise</Link></li>
                <li><Link to="/changelog" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Changelog</Link></li>
              </ul>
            </div>

            {/* Resources */}
            <div>
              <h3 className="mb-4 text-sm font-semibold text-[var(--admin-text)]">Resources</h3>
              <ul className="space-y-2">
                <li><Link to="/docs" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Documentation</Link></li>
                <li><Link to="/about" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">About</Link></li>
                <li><Link to="/contact" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Contact</Link></li>
                <li><Link to="/legal" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Legal</Link></li>
                <li>
                  <a href={CONFIG.githubUrl} target="_blank" rel="noopener noreferrer" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">GitHub</a>
                </li>
                <li>
                  <a href={CONFIG.discordUrl} target="_blank" rel="noopener noreferrer" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Discord</a>
                </li>
              </ul>
            </div>

            {/* Compliance */}
            <div>
              <h3 className="mb-4 text-sm font-semibold text-[var(--admin-text)]">Compliance</h3>
              <ul className="space-y-2">
                <li><Link to="/legal" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Terms</Link></li>
                <li><Link to="/legal" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Privacy Policy</Link></li>
                <li>
                  <a href="https://status.example.com/" target="_blank" rel="noopener noreferrer" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Status</a>
                </li>
              </ul>
            </div>

            {/* Compare */}
            <div>
              <h3 className="mb-4 text-sm font-semibold text-[var(--admin-text)]">Compare</h3>
              <ul className="space-y-2">
                <li><Link to="/compare" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">All Comparisons</Link></li>
                <li><Link to="/compare" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">OpenRouter</Link></li>
                <li><Link to="/compare" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">LiteLLM</Link></li>
                <li><Link to="/compare" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">AWS Bedrock</Link></li>
                <li><Link to="/compare" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Azure</Link></li>
              </ul>
            </div>

            {/* Models */}
            <div>
              <h3 className="mb-4 text-sm font-semibold text-[var(--admin-text)]">Models</h3>
              <ul className="space-y-2">
                <li><Link to="/models" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Text Generation</Link></li>
                <li><Link to="/models" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Vision</Link></li>
                <li><Link to="/models" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Reasoning</Link></li>
                <li><Link to="/models" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Tool Calling</Link></li>
                <li><Link to="/models" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Embeddings</Link></li>
                <li><Link to="/models" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Long Context</Link></li>
                <li><Link to="/models" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">Open Source</Link></li>
              </ul>
            </div>

            {/* Providers */}
            <div>
              <h3 className="mb-4 text-sm font-semibold text-[var(--admin-text)]">Providers</h3>
              <ul className="space-y-2">
                {listedProviders.map((provider) => (
                  <li key={provider.id}>
                    <Link to="/models" className="text-sm underline-offset-4 hover:text-[var(--admin-text)] hover:underline">
                      {provider.name}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>

        {/* Bottom bar */}
        <div className="mt-8 border-t border-[var(--admin-border)] pt-8">
          <p className="text-sm text-[var(--admin-text-muted)]">
            &copy; {new Date().getFullYear()} wiwi. All rights reserved.
          </p>
        </div>
      </div>
    </footer>
  );
}
