// Brand — the two logo assets that actually ship in public/, with the usage
// rules that matter for a small open-source project. No download links to
// files that do not exist.

import { Download } from "lucide-react";
import { Card } from "@/components/ui";

const GITHUB_ISSUES = "https://github.com/shinmentakezo07/wiwia/issues";

interface BrandAsset {
  name: string;
  description: string;
  path: string;
  filename: string;
  /** Dark preview needs a light tile and vice versa. */
  previewBg: string;
}

const BRAND_ASSETS: BrandAsset[] = [
  {
    name: "Logo mark",
    description: "The square mark on its own. Use it where the product name is already visible.",
    path: "/wiwi-mark.svg",
    filename: "wiwi-mark.svg",
    previewBg: "bg-zinc-100",
  },
  {
    name: "Full logo",
    description: "The mark plus the wordmark, on the dark surface it was drawn for.",
    path: "/wiwi-logo.png",
    filename: "wiwi-logo.png",
    previewBg: "bg-[var(--admin-bg)]",
  },
];

const GUIDELINES = [
  "Use the mark on its own only where the product name is already present.",
  "Keep clear space of at least half the mark's width on every side.",
  "Do not stretch, rotate, recolor, or add shadows, gradients, or outlines.",
  "The gateway is MIT-licensed; the name and marks are not a warranty of compatibility.",
  `For brand questions, open an issue at ${GITHUB_ISSUES.replace("https://", "")}.`,
];

export function BrandPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      <section className="text-center">
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          Brand{" "}
          <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
            assets
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          The logo files that ship with the project, plus the few rules worth following
          when you use them.
        </p>
      </section>

      <section className="grid gap-4 sm:grid-cols-2">
        {BRAND_ASSETS.map((asset) => (
          <Card key={asset.name} className="overflow-hidden">
            <div className={`flex min-h-[180px] items-center justify-center p-8 ${asset.previewBg}`}>
              <img
                src={asset.path}
                alt={`${asset.name} preview`}
                className={asset.path.endsWith(".svg") ? "h-20 w-20" : "h-20 w-auto rounded-xl"}
              />
            </div>
            <div className="p-5">
              <h2 className="mb-1 text-[16px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                {asset.name}
              </h2>
              <p className="mb-4 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                {asset.description}
              </p>
              <a
                href={asset.path}
                download={asset.filename}
                className="inline-flex min-h-11 items-center justify-center gap-2 whitespace-nowrap rounded-[10px] border border-[var(--admin-border)] bg-[var(--admin-surface)] px-3 py-2 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:border-[var(--admin-border-hover)] hover:bg-white/[0.04]"
              >
                <Download className="h-4 w-4" />
                Download
              </a>
            </div>
          </Card>
        ))}
      </section>

      <section>
        <Card className="p-6">
          <h2 className="mb-4 text-[20px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Usage guidelines
          </h2>
          <ul className="space-y-3">
            {GUIDELINES.map((guideline) => (
              <li
                key={guideline}
                className="flex items-start gap-2 text-[14px] leading-relaxed text-[var(--admin-text-muted)]"
              >
                <span className="mt-1 text-blue-400">•</span>
                <span>{guideline}</span>
              </li>
            ))}
          </ul>
        </Card>
      </section>
    </div>
  );
}
