// Animated hub-and-spoke diagram: inbound dialects → wiwi canonical IR →
// outbound providers. Extracted from the old single-page Docs.tsx so both the
// docs hub and article pages can use it. Visuals unchanged.

import { useRef } from "react";
import {
  AnimatedBeam,
  AnthropicIcon,
  GeminiIcon,
  OpenAIIcon,
  OpenRouterIcon,
} from "@/components/AnimatedBeam";
import { Card } from "@/components/ui";
import { DOC_MONO } from "./markdown-utils";

const INBOUND_NODES = [
  { name: "chat/completions", note: "OpenAI SDK" },
  { name: "responses", note: "Codex CLI" },
  { name: "messages", note: "Claude Code" },
];

const OUTBOUND_NODES = [
  { name: "openai", Icon: OpenAIIcon },
  { name: "anthropic", Icon: AnthropicIcon },
  { name: "gemini", Icon: GeminiIcon },
  { name: "openrouter", Icon: OpenRouterIcon },
].map((n) => ({ ...n, label: n.name }));

function FlowNode({
  ref,
  children,
  className = "",
}: {
  ref: React.RefObject<HTMLDivElement | null>;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      ref={ref}
      className={`group relative z-10 flex h-12 w-12 items-center justify-center rounded-full border-2 border-[var(--admin-border)] bg-[var(--admin-surface)] shadow-lg shadow-black/30 backdrop-blur-sm transition-all duration-200 hover:scale-110 hover:border-white/[0.18] ${className}`}
    >
      {children}
    </div>
  );
}

export function DocsFlowDiagram() {
  const containerRef = useRef<HTMLDivElement>(null);
  const centerRef = useRef<HTMLDivElement>(null);
  const inboundRefs = [useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null)];
  const outboundRefs = [useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null)];

  return (
    <Card className="mt-5 overflow-hidden p-0">
      <div ref={containerRef} className="relative h-[300px] w-full sm:h-[340px]">
        {/* radial glow behind center */}
        <div className="pointer-events-none absolute left-1/2 top-1/2 h-64 w-64 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[radial-gradient(circle,rgba(139,92,246,0.12)_0%,rgba(59,130,246,0.06)_40%,transparent_70%)] blur-2xl" aria-hidden />

        {/* Left: inbound dialects */}
        <div className="absolute left-4 top-1/2 flex -translate-y-1/2 flex-col gap-6 z-10 sm:left-6">
          {INBOUND_NODES.map((d, i) => (
            <div key={d.name} className="flex items-center gap-2.5">
              <FlowNode ref={inboundRefs[i]}>
                <code className="text-[9px] font-bold text-blue-300" style={{ fontFamily: DOC_MONO }}>
                  {d.name.slice(0, 1).toUpperCase()}
                </code>
              </FlowNode>
              <div className="hidden sm:block">
                <div className="text-[11px] font-mono text-blue-300">{d.name}</div>
                <div className="text-[10px] text-[var(--admin-text-dim)]">{d.note}</div>
              </div>
            </div>
          ))}
        </div>

        {/* Center: wiwi IR */}
        <div className="absolute left-1/2 top-1/2 z-10 -translate-x-1/2 -translate-y-1/2">
          <FlowNode ref={centerRef} className="!h-16 !w-16 wiwi-gateway-node">
            <img src="/wiwi-logo.png" alt="wiwi" className="h-9 w-9 rounded-full object-cover" />
          </FlowNode>
          <div className="mt-2.5 text-center">
            <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--admin-text-dim)]">wiwi IR</span>
          </div>
        </div>

        {/* Right: outbound providers */}
        <div className="absolute right-4 top-1/2 flex -translate-y-1/2 flex-col gap-5 z-10 sm:right-6">
          {OUTBOUND_NODES.map((node, i) => (
            <div key={node.name} className="flex items-center gap-2.5">
              <div className="hidden text-right sm:block">
                <div className="text-[11px] font-mono text-violet-300">{node.name}</div>
                <div className="text-[10px] text-[var(--admin-text-dim)]">provider</div>
              </div>
              <FlowNode ref={outboundRefs[i]} className="hover:!border-blue-500/40">
                <node.Icon className="h-5 w-5 object-contain" />
              </FlowNode>
            </div>
          ))}
        </div>

        {/* Animated beams: inbound → center */}
        {inboundRefs.map((ref, i) => {
          const hues = [
            ["#3b82f6", "#8b5cf6"], // chat → blue→violet
            ["#06b6d4", "#8b5cf6"], // responses → cyan→violet
            ["#a855f7", "#ec4899"], // messages → purple→pink
          ];
          const [g0, g1] = hues[i] ?? ["#3b82f6", "#8b5cf6"];
          return (
            <AnimatedBeam
              key={`in-${i}`}
              containerRef={containerRef}
              fromRef={ref}
              toRef={centerRef}
              curvature={(i - 1) * 18}
              delay={i * 0.3}
              pathWidth={2.5}
              gradientStart={g0}
              gradientStop={g1}
              duration={3 + i * 0.3}
            />
          );
        })}

        {/* Animated beams: center → outbound */}
        {outboundRefs.map((ref, i) => {
          const hues = [
            ["#8b5cf6", "#ec4899"], // openai → violet→pink
            ["#f59e0b", "#ef4444"], // anthropic → amber→red
            ["#22c55e", "#3b82f6"], // gemini → green→blue
            ["#06b6d4", "#8b5cf6"], // openrouter → cyan→violet
          ];
          const [g0, g1] = hues[i] ?? ["#8b5cf6", "#ec4899"];
          return (
            <AnimatedBeam
              key={`out-${i}`}
              containerRef={containerRef}
              fromRef={centerRef}
              toRef={ref}
              curvature={(i - 1.5) * 20}
              delay={0.5 + i * 0.3}
              pathWidth={2.5}
              gradientStart={g0}
              gradientStop={g1}
              duration={3 + i * 0.3}
            />
          );
        })}
      </div>

      {/* caption */}
      <div className="border-t border-[var(--admin-border)] px-5 py-3">
        <p className="text-center text-[11px] leading-relaxed text-[var(--admin-text-dim)]">
          Inbound dialect → wiwi canonical IR → outbound provider format · responses flow back through the same path
        </p>
      </div>
    </Card>
  );
}
