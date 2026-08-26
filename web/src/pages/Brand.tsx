// Brand — official logos, marks, and usage guidelines. Adapted from the
// llmgateway.io brand assets page in the dark design system.

import { Download } from "lucide-react";
import { Card } from "@/components/ui";

interface BrandAsset {
  name: string;
  description: string;
  svgPath: string;
  variant: "black" | "white";
}

const brandAssets: BrandAsset[] = [
  {
    name: "Logo (Black)",
    description: "Logo mark only, black version for light backgrounds",
    svgPath: "/brand/logo-black.svg",
    variant: "black",
  },
  {
    name: "Logo (White)",
    description: "Logo mark only, white version for dark backgrounds",
    svgPath: "/brand/logo-white.svg",
    variant: "white",
  },
  {
    name: "Full Logo (Black)",
    description: "Logo with text, black version",
    svgPath: "/brand/logo-with-name-black.svg",
    variant: "black",
  },
  {
    name: "Full Logo (White)",
    description: "Logo with text, white version",
    svgPath: "/brand/logo-with-name-white.svg",
    variant: "white",
  },
];

const GUIDELINES = [
  "Use the black logo on light backgrounds and white logo on dark backgrounds",
  "Maintain adequate spacing around the logo (at least 20% of logo width)",
  "Do not stretch, rotate, or alter the logo proportions",
  "Do not add effects like shadows, gradients, or outlines to the logo",
  "For questions about brand usage, contact us at contact@llmgateway.io",
];

function LogoMark({ variant }: { variant: "black" | "white" }) {
  const color = variant === "black" ? "#000000" : "#ffffff";
  return (
    <svg
      viewBox="0 0 32 32"
      className="h-12 w-12"
      fill="none"
      aria-hidden
    >
      <rect width="32" height="32" rx="8" fill={color} />
      <path
        d="M10 10h4v4h-4zM18 10h4v4h-4zM10 18h4v4h-4zM18 18h4v4h-4z"
        fill={variant === "black" ? "#fff" : "#000"}
      />
    </svg>
  );
}

function LogoWithName({ variant }: { variant: "black" | "white" }) {
  const color = variant === "black" ? "#000000" : "#ffffff";
  return (
    <div className="flex items-center gap-3">
      <LogoMark variant={variant} />
      <span className="text-2xl font-bold tracking-tight" style={{ color }}>
        LLM Gateway
      </span>
    </div>
  );
}

function BrandAssetCard({ asset }: { asset: BrandAsset }) {
  const bgColor =
    asset.variant === "white" ? "bg-[var(--admin-surface-elevated)]" : "bg-zinc-100";
  const isFull = asset.name.includes("Full");
  const svgFilename = asset.svgPath.split("/").pop() ?? "logo.svg";

  return (
    <Card className="overflow-hidden">
      <div className={`flex min-h-[160px] items-center justify-center p-8 ${bgColor}`}>
        {isFull ? <LogoWithName variant={asset.variant} /> : <LogoMark variant={asset.variant} />}
      </div>
      <div className="p-5">
        <h3 className="mb-1 text-[16px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          {asset.name}
        </h3>
        <p className="mb-4 text-[13px] text-[var(--admin-text-muted)]">{asset.description}</p>
        <a
          href={asset.svgPath}
          download={svgFilename}
          className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[10px] border border-[var(--admin-border)] bg-[var(--admin-surface)] px-3 py-2 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:border-[var(--admin-border-hover)] hover:bg-white/[0.04]"
        >
          <Download className="h-4 w-4" />
          SVG
        </a>
      </div>
    </Card>
  );
}

export function BrandPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="text-center">
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          Brand{" "}
          <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
            Assets
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          Download official logos and brand assets for your projects, presentations,
          and integrations.
        </p>
      </section>

      {/* ── assets ── */}
      <section className="grid gap-4 sm:grid-cols-2">
        {brandAssets.map((asset) => (
          <BrandAssetCard key={asset.name} asset={asset} />
        ))}
      </section>

      {/* ── guidelines ── */}
      <section>
        <Card className="p-6">
          <h2 className="mb-4 text-[20px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Brand Guidelines
          </h2>
          <ul className="space-y-3">
            {GUIDELINES.map((g) => (
              <li key={g} className="flex items-start gap-2 text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
                <span className="mt-1 text-blue-400">•</span>
                <span>{g}</span>
              </li>
            ))}
          </ul>
        </Card>
      </section>
    </div>
  );
}
