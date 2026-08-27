// Landing — public marketing front door for the gateway. Adapted from
// llmgateway.io's landing page: hero with trust badges, feature grid (tiered),
// hub-and-spoke graph, tabbed code examples, pricing strip, FAQ accordion,
// enterprise CTA, and final CTA. All in wiwi's dark design system.

import { useId, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  ArrowDown,
  BookOpen,
  Building2,
  Check,
  ChevronDown,
  Copy,
  KeyRound,
  Lock,
  Minus,
  Server,
  Terminal,
  Wallet,
  Zap,
} from "lucide-react";
import { Card } from "@/components/ui";
import { GraphSection, OpenAIIcon, AnthropicIcon, GeminiIcon, OpenRouterIcon, MoonshotIcon } from "@/components/AnimatedBeam";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

// ── Hero beam backdrop ────────────────────────────────────────────────────
// Subtle animated gradient beams drifting across the hero background. Same
// gradient-sweep language as the How It Works graph, but lighter and slower
// so it reads as ambient motion behind the hero content.
const HERO_BEAMS = [
  { d: "M 0,120 Q 300,60 600,140 T 1200,120", dur: 10, delay: 0, w: 1.5, c0: "#3b82f6", c1: "#8b5cf6" },
  { d: "M 0,220 Q 250,160 500,240 T 1200,200", dur: 12, delay: 1.5, w: 1, c0: "#8b5cf6", c1: "#ec4899" },
  { d: "M 0,320 Q 350,260 700,340 T 1200,300", dur: 14, delay: 0.8, w: 1.5, c0: "#22d3ee", c1: "#3b82f6" },
  { d: "M 0,80 Q 400,20 800,100 T 1200,60", dur: 11, delay: 2, w: 1, c0: "#a78bfa", c1: "#22d3ee" },
  { d: "M 0,160 Q 200,100 500,180 T 1200,140", dur: 15, delay: 1.2, w: 1, c0: "#f472b6", c1: "#a78bfa" },
  { d: "M 0,280 Q 450,220 800,300 T 1200,260", dur: 13, delay: 0.5, w: 1, c0: "#60a5fa", c1: "#22c55e" },
] as const;

function HeroBeamBackdrop() {
  const id = useId().replace(/[:]/g, "");
  return (
    <svg
      className="pointer-events-none absolute inset-0 h-full w-full opacity-70"
      viewBox="0 0 1200 400"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden
    >
      <defs>
        {HERO_BEAMS.map((b, i) => (
          <linearGradient key={i} id={`hb-${id}-${i}`} gradientUnits="userSpaceOnUse">
            <stop stopColor={b.c0} stopOpacity="0">
              <animate attributeName="offset" values="-0.3;1" dur={`${b.dur}s`} begin={`${b.delay}s`} repeatCount="indefinite" />
            </stop>
            <stop stopColor={b.c0}>
              <animate attributeName="offset" values="-0.1;1.1" dur={`${b.dur}s`} begin={`${b.delay}s`} repeatCount="indefinite" />
            </stop>
            <stop offset="0.3" stopColor={b.c1}>
              <animate attributeName="offset" values="0;1.3" dur={`${b.dur}s`} begin={`${b.delay}s`} repeatCount="indefinite" />
            </stop>
            <stop offset="1" stopColor={b.c1} stopOpacity="0">
              <animate attributeName="offset" values="0.3;1.6" dur={`${b.dur}s`} begin={`${b.delay}s`} repeatCount="indefinite" />
            </stop>
          </linearGradient>
        ))}
      </defs>
      {HERO_BEAMS.map((b, i) => (
        <g key={i}>
          <path d={b.d} fill="none" stroke="rgba(255,255,255,0.03)" strokeWidth={b.w} />
          <path d={b.d} fill="none" stroke={`url(#hb-${id}-${i})`} strokeWidth={b.w} strokeLinecap="round" />
        </g>
      ))}
    </svg>
  );
}

// ── data ───────────────────────────────────────────────────────────────────

const STATS = [
  { value: "40+", label: "Providers" },
  { value: "200+", label: "Models" },
  { value: "100B+", label: "Tokens routed" },
  { value: "99.9999%", label: "Uptime" },
];

// ── Uptime / Reliability data ──────────────────────────────────────────────

const UPTIME_PROVIDERS = [
  { name: "Anthropic", outages: [[8, 10], [42, 44], [78, 80]] as [number, number][] },
  { name: "AWS Bedrock", outages: [[3, 5], [30, 32], [63, 65]] as [number, number][] },
  { name: "Google Vertex", outages: [[18, 20], [50, 52], [72, 74]] as [number, number][] },
  { name: "Azure OpenAI", outages: [[13, 15], [37, 39], [86, 88]] as [number, number][] },
  { name: "Fireworks AI", outages: [[23, 25], [55, 57], [95, 97]] as [number, number][] },
];

function buildSegments(outages: [number, number][]) {
  const segments: { type: "up" | "down"; width: number }[] = [];
  let pos = 0;
  for (const [start, end] of outages) {
    if (start > pos) segments.push({ type: "up", width: start - pos });
    segments.push({ type: "down", width: end - start });
    pos = end;
  }
  if (pos < 100) segments.push({ type: "up", width: 100 - pos });
  return segments;
}

// ── Testimonials data ──────────────────────────────────────────────────────

const TESTIMONIALS = [
  { handle: "@awakecoding", name: "Marc-André Moreau", text: "I found exactly what I was looking for: an @llmgateway DevPass MAX subscription that gives me 3X the amount of tokens for what I would normally pay with regular API pricing. There's a weekly limit for premium models, but that's the only limitation!" },
  { handle: "@pxng0lin", name: "pxng0lin", text: "@llmgateway is my new OpenRouter. The total models are less, but I luv the integration with my projects, the response and help from the team and the cost (or rather lack of) to use it with Claude. I'm currently on $0.44 spending running my workflow with Claude Code! Win!" },
  { handle: "@dabit3", name: "nader dabit", text: "LLM Gateway - the Open Source Alternative of OpenRouter @llmgateway feat. @smakosh and @steebchen" },
  { handle: "@montekkundan", name: "Montek", text: "adding all 93 models to chaichat by @llmgateway. It would be easier to experiment with all models and you just need one api key! will be live on dev site soon. thanks @steebchen for the help!" },
  { handle: "@stormix_dev", name: "Stormix", text: "Known @steebchen and @smakosh for 8+ years! They've always shipped great stuff. Their latest project @llmgateway is no exception 🚀" },
  { handle: "@ossalternative", name: "OpenAlternative", text: "🚀 Just published: LLM Gateway @llmgateway — Unified API for all LLM providers with analytics. Route, manage, and analyze LLM requests across multiple providers with one API." },
  { handle: "@Andy_AJT", name: "Andy T", text: "Wow. What a night, incredible builders, incredible venue & incredible team. The AI Hack Night was a complete success! Feeling incredibly grateful to @llmgateway for making it so easy for us to hack with any model." },
  { handle: "@iTanayVaswani", name: "Tanay Vaswani", text: "List 10 great open-source repositories 👇🏻 I will go first: - @calcom - @mail0dotcom - @cap - @documenso - @aisdk (by @vercel) - @onyx_dot_app - @daytonaio - @langfuse - @llmgateway - @qdrant_engine" },
];

// ── Enterprise capabilities ────────────────────────────────────────────────

const ENTERPRISE_CAPS = [
  { icon: Lock, title: "Enterprise SSO", description: "SAML & OIDC single sign-on with role-based access control" },
  { icon: Server, title: "Self-hosted or Managed", description: "Deploy on your infrastructure or let us handle it with 99.9% SLA" },
  { icon: Zap, title: "Volume Pricing", description: "Custom rate limits and pricing that scales with your usage" },
  { icon: Building2, title: "White-label Ready", description: "White-label gateway and chat app with your own branding" },
];

// ── Platform Capabilities (ported from llmgateway-ref features.tsx) ────────

const features: { icon: React.ReactNode; title: string; description: string; slug: string }[] = [
  {
    icon: (
      <svg className="h-8 w-8" fill="none" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 148 139">
        <path d="M0 37C0 16.5655 16.5655 0 37 0h73.217c20.434 0 37 16.5655 37 37v65c0 20.435-16.566 37-37 37H37c-20.4345 0-37-16.565-37-37V37Z" fill="#626264" />
        <path d="M69.5 73.266h8.9015c-.913 6.1626.4793 18.1453 13.3522 16.7758v18.1452c.1141 1.484 1.3695 4.656 5.4778 5.478h22.5965c1.711-.685 5.204-2.739 5.477-5.478V87.3029c-.228-2.1683-1.643-6.5049-5.477-6.5049H91.7537c-1.4836-.2282-3.766-2.3965-3.766-6.5049v-9.5862c0-4.1084 2.2824-6.2767 3.766-6.5049h28.0743c3.834 0 5.249-4.3367 5.477-6.505V30.8128c-.273-2.7389-3.766-4.7931-5.477-5.4778H97.2315c-4.1083.8216-5.3637 3.9942-5.4778 5.4778v18.1453C78.8808 47.5887 77.4885 59.5714 78.4015 65.734H69.5c.913-6.1626-.4793-18.1453-13.3522-16.7759V30.8128c-.1141-1.4836-1.3695-4.6562-5.4778-5.4778H28.0739c-1.7118.6847-5.2039 2.7389-5.4778 5.4778V51.697c.2282 2.1683 1.6433 6.505 5.4778 6.505h28.0739c1.4836.2282 3.766 2.3965 3.766 6.5049v9.5862c0 4.1084-2.2824 6.2767-3.766 6.5049H28.0739c-3.8345 0-5.2496 4.3366-5.4778 6.5049v20.8841c.2739 2.739 3.766 4.793 5.4778 5.478H50.67c4.1083-.822 5.3637-3.994 5.4778-5.478V90.0418C69.0207 91.4113 70.413 79.4286 69.5 73.266Z" fill="#D0D0C6" />
        <path d="M32.1823 36.5517c0-1.6568 1.3431-3 3-3h8.3793c1.6568 0 3 1.3432 3 3v8.3793c0 1.6569-1.3432 3-3 3h-8.3793c-1.6569 0-3-1.3431-3-3v-8.3793ZM32.1823 93.3842c0-1.6568 1.3431-3 3-3h8.3793c1.6568 0 3 1.3432 3 3v8.3798c0 1.656-1.3432 3-3 3h-8.3793c-1.6569 0-3-1.344-3-3v-8.3798ZM101.34 36.5517c0-1.6568 1.343-3 3-3h8.379c1.657 0 3 1.3432 3 3v8.3793c0 1.6569-1.343 3-3 3h-8.379c-1.657 0-3-1.3431-3-3v-8.3793ZM101.34 93.3842c0-1.6568 1.343-3 3-3h8.379c1.657 0 3 1.3432 3 3v8.3798c0 1.656-1.343 3-3 3h-8.379c-1.657 0-3-1.344-3-3v-8.3798Z" fill="#4D4D4B" />
      </svg>
    ),
    title: "Unified API Interface",
    description: "Keep your existing OpenAI SDK code—just change the base URL and you're live.",
    slug: "unified-api-interface",
  },
  {
    icon: (
      <svg className="h-8 w-8" fill="none" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 161 162">
        <path d="M23.0177 83.7271c1.2867.7188 2.4615.7548 3.7173.1867 2.8495-1.2891 3.7334-4.781 4.0121-7.8961 1.8361-20.5263 15.1133-28.817 21.9943-30.4143v4.0142c0 1.0129.1453 2.0784.823 2.8313 1.9947 2.2161 4.935.2011 6.3149-1.2938l9.9138-9.5172c2.5987-2.4255 2.0358-5.1903.8612-6.7116-.4863-.6297-1.1446-1.1063-1.7072-1.6689l-9.0678-9.0678c-2.6759-2.6759-4.901-1.6366-6.2104-.172-.7243.8102-.9275 1.9274-.9275 3.0141v5.4855c-24.4947 7.6902-31.8565 30.4463-32.8183 44.1047-.1946 2.7635.6759 5.7542 3.0946 7.1052Z" fill="#48474F" />
        <circle cx="107.069" cy="31.7241" r="31.7241" fill="#E9A92E" />
        <circle cx="131.259" cy="125.707" r="29.7414" fill="#CC595E" />
        <circle cx="34.8966" cy="125.31" r="34.8966" fill="#5891D1" />
        <path d="M137.617 94.0779c7.388-3.5978 3.803-18.7268.621-27.183-.393-1.046-1.058-1.9757-1.945-2.6551-2.966-2.2705-5.081-2.8945-7.216-1.9595-2.68 1.1738-2.826 4.7445-1.856 7.5047 2.248 6.3907 2.652 12.7538 2.52 15.5761-.719 8.8109 5.766 9.6595 7.876 8.7168ZM97.1552 140.379c4.7588-1.586 5.9478 3.834 5.9478 6.742s-1.189 8.327-5.9478 6.741v5.552c-1.3219 1.454-4.6793 3.727-7.5345 1.189l-13.7363-11.975c-.9143-.797-.9143-2.218 0-3.015l13.7363-11.975c2.8552-2.538 6.2126-.264 7.5345 1.19v5.551Z" fill="#48474F" />
      </svg>
    ),
    title: "Multi-provider Support",
    description: "Access 40+ providers through one integration—no vendor lock-in, switch models instantly.",
    slug: "multi-provider-support",
  },
  {
    icon: (
      <svg className="h-8 w-8" fill="none" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 161 161">
        <circle cx="80.5" cy="80.5" r="61.457" fill="#C9C4BE" />
        <path d="M79.3967 16.4772c9.7693-.0828 19.4297 2.1253 28.2543 6.459 8.824 4.3337 16.583 10.6798 22.691 18.5606 6.108 7.8807 10.407 17.0911 12.574 26.9375 1.548 7.0349 1.979 14.2639 1.293 21.4059-.453 4.7166-5.128 7.5983-9.736 6.4926-4.511-1.0825-7.243-5.5932-6.986-10.225.053-.9481.08-1.9033.08-2.865 0-27.1944-21.33-49.2402-47.641-49.2402-26.3109.0001-47.6397 22.0459-47.6397 49.2402 0 1.2363.0443 2.4618.1311 3.6751.331 4.6252-2.3245 9.1778-6.8146 10.3361-4.5924 1.1846-9.3198-1.6229-9.8475-6.3362-.7977-7.1257-.4816-14.3569.9539-21.4132 2.0102-9.8817 6.1631-19.1633 12.1455-27.1465 5.9825-7.9833 13.6392-14.4605 22.3935-18.9434 8.7544-4.4828 18.379-6.8546 28.1485-6.9375Z" fill="url(#feat-gauge)" />
        <path d="M80.5 0C124.959 0 161 36.0411 161 80.5c0 44.459-36.041 80.5-80.5 80.5C36.0411 161 0 124.959 0 80.5 0 36.0411 36.0411 0 80.5 0Zm0 19.043c-33.9418 0-61.457 27.5152-61.457 61.457 0 33.942 27.5153 61.457 61.457 61.457 33.942 0 61.457-27.515 61.457-61.457 0-33.9417-27.515-61.457-61.457-61.457Z" fill="#626264" />
        <circle cx="80.5001" cy="80.5" r="9.08871" fill="#000" />
        <path d="M78.7973 81.3148c-2.1067-2.6761-1.46-6.5836 1.3966-8.4383l31.5091-20.4577c1.114-.7236 2.597-.4783 3.419.5658.828 1.0527.709 2.5654-.274 3.4756l-27.5671 25.522c-2.4805 2.2965-6.3926 1.9888-8.4836-.6674Z" fill="#000" />
        <defs>
          <linearGradient id="feat-gauge" x1="23.0599" y1="63.7339" x2="137.463" y2="63.7339" gradientUnits="userSpaceOnUse">
            <stop stopColor="#E25137" /><stop offset=".134763" stopColor="#E96131" /><stop offset=".293302" stopColor="#E29231" /><stop offset=".441502" stopColor="#E2A635" /><stop offset=".569022" stopColor="#D0AC3E" /><stop offset=".699989" stopColor="#8CBF3F" /><stop offset=".834403" stopColor="#2E9E4E" /><stop offset="1" stopColor="#178977" />
          </linearGradient>
        </defs>
      </svg>
    ),
    title: "Performance Monitoring",
    description: "Compare latency, cost, and quality across models to pick the best fit for each use case.",
    slug: "performance-monitoring",
  },
  {
    icon: (
      <svg className="h-8 w-8" fill="none" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 129 152">
        <path d="M118.381 21.1753 70.5943 1.62615c-4.9904-2.013897-7.8284-2.317207-13.0328 0L9.77459 21.1753l-.0891.0474C5.82278 23.2738 0 26.3657 0 42.1726v36.2022c0 20.6417 18.8226 60.8432 60.4576 72.3802 1.4143.392 2.9281.392 4.3427.001 41.7327-11.535 63.3557-51.7388 63.3557-72.3812V42.1726c0-15.8069-5.823-18.8988-9.686-20.9499l-.089-.0474Z" fill="#616163" />
        <path d="m66.2822 65.1639 7.0841 7.394 7.0841 7.3939-47.633 45.6372c-5.0184 4.808-13.018 8.254-18.2735 3.706-.5899-.51-1.1093-1.085-1.5611-1.739-3.43011-4.963.1412-11.461 4.4979-15.635l48.8015-46.7571Z" fill="#E6B747" />
        <path d="M52.2287 132.374c-.4652 1.972-1.3275 3.355-2.8848 4.234-4.1117 2.323-9.0459-.669-12.3609-4.032l-6.1944-6.284 25.6352-23.841 5.713 5.796c3.5601 3.611 6.7166 9.897 2.7433 13.048-.3041.241-.6408.449-1.0152.626-3.0352 1.428-6.4373-.596-8.7922-2.985l-5.6571 5.421c2.1087 2.139 3.5026 5.093 2.8131 8.017Z" fill="#E6B747" />
        <ellipse cx="90.8676" cy="57.9235" rx="30.0478" ry="28.9618" fill="#E6B747" />
        <ellipse cx="91.2293" cy="57.5615" rx="10.8607" ry="11.2227" fill="#616163" />
      </svg>
    ),
    title: "Secure Key Management",
    description: "One dashboard for all your provider keys—no more scattered credentials or exposed secrets.",
    slug: "secure-key-management",
  },
  {
    icon: (
      <svg className="h-8 w-8" fill="none" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 144 141">
        <path d="m21.3493 85.4372 49.8235 5.7766 1.0679-.0084 49.7503-5.7682c8.206-.6034 23.894-7.8197 20.998-31.858.603-8.4471-5.141-25.4139-32.944-25.7035C105.459 17.2564 91.5915 0 72.0322 0h-.7239C52.1312 0 37.8816 17.2564 33.2959 27.8757 5.49266 28.1653-.251398 45.1321.351984 53.5792-2.54419 77.6175 13.1434 84.8338 21.3493 85.4372Z" fill="#D4CFCB" />
        <path d="M17.377 78.7269c0-6.0751 4.9248-11 11-11h87.331c6.075 0 11 4.9249 11 11v14.2022c0 6.0751-4.925 10.9999-11 10.9999H28.377c-6.0752 0-11-4.9248-11-10.9999V78.7269ZM17.377 115.653c0-6.075 4.9248-11 11-11h87.331c6.075 0 11 4.925 11 11v14.202c0 6.075-4.925 11-11 11H28.377c-6.0752 0-11-4.925-11-11v-14.202Z" fill="#59595B" />
        <rect x="28.2378" y="79.3116" width="49.959" height="13.0328" rx="6.51639" fill="#2F3032" />
        <rect x="28.2378" y="117.686" width="49.959" height="13.0328" rx="6.51639" fill="#2F3032" />
        <circle cx="107.521" cy="84.7419" r="5.43033" fill="#77B359" />
        <circle cx="106.797" cy="126.012" r="3.98224" fill="#77B359" />
      </svg>
    ),
    title: "Self-hosted or Cloud",
    description: "Run on your own infrastructure for full control, or let us handle it—your choice.",
    slug: "self-hosted-or-cloud",
  },
  {
    icon: (
      <svg className="h-8 w-8" fill="none" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 144 144">
        <rect x="10" y="20" width="124" height="94" rx="16" fill="#0F172A" />
        <rect x="24" y="34" width="96" height="6" rx="3" fill="#4ADE80" />
        <rect x="24" y="50" width="72" height="6" rx="3" fill="#38BDF8" />
        <rect x="24" y="66" width="54" height="6" rx="3" fill="#A855F7" />
        <circle cx="40" cy="96" r="10" fill="#22C55E" />
        <path d="M40 90c-3.3137 0-6 2.6863-6 6s2.6863 6 6 6 6-2.6863 6-6" stroke="#DCFCE7" strokeWidth="2" strokeLinecap="round" />
      </svg>
    ),
    title: "Cost-aware analytics",
    description: "See requests, tokens, total spend, and average cost per 1K tokens across 7 or 30 days.",
    slug: "cost-aware-analytics",
  },
  {
    icon: (
      <svg className="h-8 w-8" fill="none" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 144 144">
        <rect x="14" y="22" width="116" height="100" rx="12" fill="#020617" />
        <rect x="30" y="40" width="20" height="54" rx="4" fill="#38BDF8" />
        <rect x="62" y="30" width="20" height="64" rx="4" fill="#A855F7" />
        <rect x="94" y="52" width="20" height="42" rx="4" fill="#22C55E" />
        <circle cx="38" cy="106" r="3" fill="#38BDF8" />
        <circle cx="70" cy="106" r="3" fill="#A855F7" />
        <circle cx="102" cy="106" r="3" fill="#22C55E" />
      </svg>
    ),
    title: "Per-model/provider breakdown",
    description: "Break down usage and spend by provider and model so you can quickly spot expensive outliers.",
    slug: "per-model-provider-breakdown",
  },
  {
    icon: (
      <svg className="h-8 w-8" fill="none" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 144 144">
        <rect x="16" y="24" width="112" height="96" rx="12" fill="#020617" />
        <path d="M36 92c8 0 8-16 16-16s8 16 16 16 8-16 16-16 8 16 16 16" stroke="#22C55E" strokeWidth="3" strokeLinecap="round" />
        <circle cx="40" cy="52" r="6" fill="#F97316" />
        <circle cx="72" cy="44" r="6" fill="#F97316" />
        <circle cx="104" cy="60" r="6" fill="#F97316" />
      </svg>
    ),
    title: "Errors & reliability monitoring",
    description: "Monitor error rate, cache hit rate, and reliability trends directly from the dashboard.",
    slug: "errors-reliability-monitoring",
  },
  {
    icon: (
      <svg className="h-8 w-8" fill="none" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 144 144">
        <rect x="14" y="24" width="116" height="96" rx="12" fill="#020617" />
        <rect x="30" y="40" width="84" height="14" rx="4" fill="#0EA5E9" />
        <rect x="30" y="62" width="56" height="10" rx="3" fill="#4ADE80" />
        <rect x="30" y="80" width="72" height="10" rx="3" fill="#A855F7" />
        <circle cx="40" cy="102" r="4" fill="#4ADE80" />
        <circle cx="60" cy="102" r="4" fill="#38BDF8" />
        <circle cx="80" cy="102" r="4" fill="#F97316" />
      </svg>
    ),
    title: "Project-level usage explorer",
    description: "Drill into each project's requests, models, errors, cache, and costs with dedicated charts and tables.",
    slug: "project-level-usage-explorer",
  },
  {
    icon: (
      <svg className="h-8 w-8" fill="none" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 144 144">
        <rect x="24" y="16" width="96" height="112" rx="8" fill="#616163" />
        <rect x="34" y="28" width="76" height="88" rx="4" fill="#D4CFCB" />
        <rect x="44" y="42" width="56" height="6" rx="2" fill="#4ADE80" />
        <rect x="44" y="56" width="40" height="6" rx="2" fill="#38BDF8" />
        <rect x="44" y="70" width="48" height="6" rx="2" fill="#A855F7" />
        <rect x="44" y="84" width="36" height="6" rx="2" fill="#F97316" />
        <rect x="44" y="98" width="52" height="6" rx="2" fill="#22C55E" />
        <circle cx="44" cy="45" r="2" fill="#020617" />
        <circle cx="44" cy="59" r="2" fill="#020617" />
        <circle cx="44" cy="73" r="2" fill="#020617" />
        <circle cx="44" cy="87" r="2" fill="#020617" />
        <circle cx="44" cy="101" r="2" fill="#020617" />
      </svg>
    ),
    title: "Enterprise Audit Logs",
    description: "Track who did what, when, and maintain compliance with comprehensive audit trails.",
    slug: "audit-logs",
  },
  {
    icon: (
      <svg className="h-8 w-8" fill="none" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 144 144">
        <path d="M72 12L24 32v40c0 33.137 21.49 62.627 48 72 26.51-9.373 48-38.863 48-72V32L72 12Z" fill="#616163" />
        <path d="M72 24L36 40v28c0 26.51 17.192 50.102 36 57.6 18.808-7.498 36-31.09 36-57.6V40L72 24Z" fill="#4ADE80" />
        <path d="M64 72l-8-8-6 6 14 14 24-24-6-6-18 18Z" fill="#020617" />
      </svg>
    ),
    title: "LLM Guardrails",
    description: "Prevent prompt injection, detect PII, and block malicious requests with intelligent guardrails.",
    slug: "guardrails",
  },
];

const tier1Features = features.slice(0, 3);
const tier2Features = features.slice(3);

const CODE_EXAMPLES: { label: string; code: string }[] = [
  {
    label: "curl",
    code: `curl -X POST https://api.llmgateway.io/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer $LLM_GATEWAY_API_KEY" \\
  -d '{
  "model": "gpt-4o",
  "messages": [
    {"role": "user", "content": "Hello, how are you?"}
  ]
}'`,
  },
  {
    label: "typescript",
    code: `import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.LLM_GATEWAY_API_KEY,
  baseURL: "https://api.llmgateway.io/v1/"
});

const response = await client.chat.completions.create({
  model: "gpt-4o",
  messages: [
    { role: "user", content: "Hello, how are you?" }
  ]
});

console.log(response.choices[0].message.content);`,
  },
  {
    label: "next.js",
    code: `import { createLLMGateway } from "@llmgateway/ai-sdk-provider";
import { generateText } from 'ai';

const llmgateway = createLLMGateway({ apiKey });

const { text } = await generateText({
  model: llmgateway('openai/gpt-4o'),
  prompt: 'Write a vegetarian lasagna recipe for 4 people.',
});`,
  },
  {
    label: "python",
    code: `import openai

client = openai.OpenAI(
    api_key="YOUR_LLM_GATEWAY_API_KEY",
    base_url="https://api.llmgateway.io/v1"
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello, how are you?"}]
)
print(response.choices[0].message.content)`,
  },
  {
    label: "java",
    code: `import com.theokanning.openai.OpenAiService;
import com.theokanning.openai.completion.chat.*;

import java.util.List;

public class Main {
    public static void main(String[] args) {
        String apiKey = System.getenv("LLM_GATEWAY_API_KEY");
        OpenAiService service = new OpenAiService(apiKey, 60);
        service.setOpenAiApiUrl("https://api.llmgateway.io/v1/");

        ChatMessage message = new ChatMessage("user", "Hello, how are you?");
        ChatCompletionRequest request = ChatCompletionRequest.builder()
            .model("gpt-4o")
            .messages(List.of(message))
            .build();

        ChatCompletionResult result = service.createChatCompletion(request);
        System.out.println(result.getChoices().get(0).getMessage().getContent());
    }
}`,
  },
  {
    label: "rust",
    code: `use openai_api_rs::v1::chat::{ChatCompletionMessage, ChatCompletionRequest};
use openai_api_rs::v1::OpenAI;
use std::env;

#[tokio::main]
async fn main() {
    let api_key = env::var("LLM_GATEWAY_API_KEY").unwrap();
    let openai = OpenAI::new(&api_key).with_base_url("https://api.llmgateway.io/v1");

    let request = ChatCompletionRequest::new(
        "gpt-4o",
        vec![ChatCompletionMessage::user("Hello, how are you?")]
    );

    let response = openai.chat().create(request).await.unwrap();
    println!("{}", response.choices[0].message.content);
}`,
  },
  {
    label: "go",
    code: `package main

import (
    "context"
    "fmt"
    "os"

    openai "github.com/sashabaranov/go-openai"
)

func main() {
    client := openai.NewClientWithConfig(openai.DefaultConfig(
        os.Getenv("LLM_GATEWAY_API_KEY"),
        "https://api.llmgateway.io/v1",
    ))
    resp, err := client.CreateChatCompletion(
        context.Background(),
        openai.ChatCompletionRequest{
            Model: "gpt-4o",
            Messages: []openai.ChatCompletionMessage{
                {Role: openai.ChatMessageRoleUser, Content: "Hello, how are you?"},
            },
        },
    )
    if err != nil { panic(err) }
    fmt.Println(resp.Choices[0].Message.Content)
}`,
  },
  {
    label: "php",
    code: `<?php
require 'vendor/autoload.php';

$client = OpenAI::client('YOUR_LLM_GATEWAY_API_KEY', [
    'base_uri' => 'https://api.llmgateway.io/v1',
]);

$response = $client->chat()->create([
    'model' => 'gpt-4o',
    'messages' => [
        ['role' => 'user', 'content' => 'Hello, how are you?']
    ],
]);

echo $response['choices'][0]['message']['content'];
?>`,
  },
  {
    label: "ruby",
    code: `require "openai"

client = OpenAI::Client.new(
  access_token: ENV["LLM_GATEWAY_API_KEY"],
  uri_base: "https://api.llmgateway.io/v1"
)

response = client.chat(
  parameters: {
    model: "gpt-4o",
    messages: [{ role: "user", content: "Hello, how are you?" }]
  }
)

puts response.dig("choices", 0, "message", "content")`,
  },
];

const PRICING = [
  {
    icon: Wallet,
    name: "Credits",
    price: "5% flat fee",
    description: "Pay-as-you-go credits for any model at provider rates, with a flat platform fee on top-ups. No subscription, no markup on tokens.",
  },
  {
    icon: KeyRound,
    name: "Bring your own keys",
    price: "Free",
    description: "Route through your own provider API keys and pay providers directly. Routing, tracking, and analytics included at no cost.",
    featured: true,
  },
  {
    icon: Server,
    name: "Self-host",
    price: "Free forever",
    description: "Deploy the AGPLv3-licensed gateway on your own infrastructure. The full routing layer, yours to run.",
  },
];

const FAQ = [
  {
    q: "What makes LLM Gateway different from OpenRouter?",
    a: "Unlike OpenRouter, we offer: Full self-hosting under an AGPLv3 license – run the gateway entirely on your infra. Deeper, real-time cost & latency analytics for every request. Bring Your Own Keys – use your own provider API keys for free. Flexible enterprise add-ons (dedicated shard, custom SLAs).",
  },
  {
    q: "What models do you support?",
    a: "We support 200+ models across 40+ providers—including GPT-4o, Claude, Gemini, Llama, Mistral, and more. We add new releases within 48 hours of launch.",
  },
  {
    q: "What is your uptime guarantee?",
    a: "Our public status page posts real-time metrics. Enterprise instances come with a 99.9% uptime SLA; self-host installations depend on your infrastructure.",
  },
  {
    q: "How much does it cost?",
    a: "Credits: Pay-as-you-go with a flat 5% platform fee. BYOK: Use your own provider API keys for free. Enterprise: Custom SLA, dedicated infrastructure, and volume discounts. Self-host: Deploy free forever under AGPLv3 license.",
  },
];

const COMPARISON = [
  { feature: "Self-hosted", wiwi: true, openrouter: false, litellm: true, portkey: true },
  { feature: "Open source", wiwi: true, openrouter: false, litellm: true, portkey: false },
  { feature: "Three inbound dialects", wiwi: true, openrouter: false, litellm: false, portkey: false },
  { feature: "Virtual keys", wiwi: true, openrouter: true, litellm: true, portkey: true },
  { feature: "Key pools + weighted round-robin", wiwi: true, openrouter: true, litellm: true, portkey: true },
  { feature: "Per-key budgets & rate limits", wiwi: true, openrouter: false, litellm: true, portkey: true },
  { feature: "Cost tracking per key/model/provider", wiwi: true, openrouter: true, litellm: true, portkey: true },
  { feature: "Retries & fallbacks", wiwi: true, openrouter: true, litellm: true, portkey: true },
  { feature: "Admin web UI", wiwi: true, openrouter: false, litellm: true, portkey: true },
  { feature: "No token markup", wiwi: true, openrouter: false, litellm: true, portkey: false },
];

// ── small components ───────────────────────────────────────────────────────

function CodeTabs() {
  const [idx, setIdx] = useState(0);
  const [copied, setCopied] = useState(false);
  const tab = CODE_EXAMPLES[idx];
  return (
    <div className="relative">
      <div className="flex items-center gap-1 rounded-[10px] border border-[var(--admin-border)] bg-[var(--admin-surface)] p-1">
        {CODE_EXAMPLES.map((t, i) => (
          <button
            key={t.label}
            onClick={() => { setIdx(i); setCopied(false); }}
            className={`rounded-md px-3.5 py-1.5 text-[12px] font-medium transition-colors ${
              i === idx
                ? "bg-blue-500/10 text-blue-300"
                : "text-[var(--admin-text-dim)] hover:bg-white/[0.03] hover:text-[var(--admin-text-muted)]"
            }`}
          >
            {t.label}
          </button>
        ))}
        <button
          onClick={async () => { await navigator.clipboard.writeText(tab.code); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
          className="ml-auto flex items-center gap-1 rounded-md px-2.5 py-1.5 text-[11px] text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)]"
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <div className="mt-2.5 overflow-hidden rounded-[10px] border border-[var(--admin-border)] bg-[var(--admin-surface)]">
        <div className="flex items-center gap-1.5 border-b border-[var(--admin-border)] px-3.5 py-2">
          <span className="h-2.5 w-2.5 rounded-full bg-red-500/60" />
          <span className="h-2.5 w-2.5 rounded-full bg-amber-500/60" />
          <span className="h-2.5 w-2.5 rounded-full bg-green-500/60" />
          <span className="ml-2 admin-label text-[10px]">{tab.label}</span>
        </div>
        <pre className="overflow-x-auto px-3.5 py-3 text-[12px] leading-relaxed" style={{ fontFamily: MONO }}>
          <code className="text-[var(--admin-text-muted)]">{tab.code}</code>
        </pre>
      </div>
    </div>
  );
}

function CheckIcon({ yes }: { yes: boolean }) {
  return yes ? (
    <Check size={14} className="text-emerald-400" />
  ) : (
    <Minus size={14} className="text-[var(--admin-text-dim)]" />
  );
}

// ── page ───────────────────────────────────────────────────────────────────

export function LandingPage() {
  return (
    <div className="space-y-16 pb-24">
      {/* ══ hero ══ */}
      <section className="full-bleed relative flex min-h-[calc(100vh-64px)] items-center justify-center overflow-hidden bg-[var(--admin-bg)] px-6 sm:px-12">
        {/* Animated beam backdrop — the sole ambient effect on pure black */}
        <HeroBeamBackdrop />

        <div className="animate-hero-enter relative mx-auto -mt-8 max-w-4xl text-center">
          <h1 className="text-5xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-6xl lg:text-7xl">
            One gateway,
            <br />
            <span className="hero-gradient-text">every model</span>
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-[15px] leading-relaxed text-[var(--admin-text-muted)] sm:text-base">
            wiwi speaks every inbound dialect — OpenAI, Anthropic, Codex CLI — and routes
            through one canonical IR to any provider. Virtual keys, budgets, key pools,
            retries, and live observability, all in one binary.
          </p>
          <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
            <div className="relative">
              <div className="hero-cta-ring" aria-hidden />
              <Link to="/signup" className="wiwi-shimmer group inline-flex h-11 items-center justify-center gap-2 rounded-full bg-gradient-to-b from-brand-400 to-brand-700 px-7 text-sm font-medium text-white shadow-lg shadow-brand-600/25 transition-[transform,filter] duration-150 hover:brightness-110 active:scale-[0.98]">
                Get my API key
                <ArrowRight size={15} className="transition-transform duration-150 group-hover:translate-x-0.5" />
              </Link>
            </div>
            <Link to="/playground" className="inline-flex h-11 items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-5 text-sm font-medium text-[var(--admin-text)] transition-colors hover:border-white/[0.14] hover:bg-white/[0.04]">
              <Terminal size={15} /> Try the playground
            </Link>
            <Link to="/docs" className="inline-flex h-11 items-center justify-center gap-2 rounded-[10px] px-5 text-sm font-medium text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.02] hover:text-[var(--admin-text)]">
              <BookOpen size={15} /> Read the docs
            </Link>
          </div>
          {/* trust indicators */}
          <div className="mt-6 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-[13px] text-[var(--admin-text-muted)]">
            <span className="flex items-center gap-1.5">
              <Check size={14} className="text-emerald-400" /> Bring your own keys — free forever
            </span>
            <span className="flex items-center gap-1.5">
              <Check size={14} className="text-emerald-400" /> No credit card required
            </span>
            <span className="flex items-center gap-1.5">
              <Check size={14} className="text-emerald-400" /> Setup in 30 seconds
            </span>
          </div>
          {/* provider badges */}
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2.5">
            <div className="flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.02] px-3.5 py-1.5 transition-colors hover:border-white/[0.12] hover:bg-white/[0.04]">
              <OpenAIIcon className="h-4 w-4" />
              <span className="text-[13px] font-medium text-[var(--admin-text)]">OpenAI</span>
            </div>
            <div className="flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.02] px-3.5 py-1.5 transition-colors hover:border-white/[0.12] hover:bg-white/[0.04]">
              <AnthropicIcon className="h-4 w-4" />
              <span className="text-[13px] font-medium text-[var(--admin-text)]">Anthropic</span>
            </div>
            <div className="flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.02] px-3.5 py-1.5 transition-colors hover:border-white/[0.12] hover:bg-white/[0.04]">
              <GeminiIcon className="h-4 w-4" />
              <span className="text-[13px] font-medium text-[var(--admin-text)]">Gemini</span>
            </div>
            <div className="flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.02] px-3.5 py-1.5 transition-colors hover:border-white/[0.12] hover:bg-white/[0.04]">
              <OpenRouterIcon className="h-4 w-4 text-[#C8FF00]" />
              <span className="text-[13px] font-medium text-[var(--admin-text)]">OpenRouter</span>
            </div>
            <div className="flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.02] px-3.5 py-1.5 transition-colors hover:border-white/[0.12] hover:bg-white/[0.04]">
              <Terminal size={14} className="text-[var(--admin-text-muted)]" />
              <span className="text-[13px] font-medium text-[var(--admin-text)]">OpenAI-compatible</span>
            </div>
            <div className="flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.02] px-3.5 py-1.5 transition-colors hover:border-white/[0.12] hover:bg-white/[0.04]">
              <MoonshotIcon className="h-4 w-4 text-[var(--admin-text)]" />
              <span className="text-[13px] font-medium text-[var(--admin-text)]">Moonshot AI</span>
            </div>
          </div>
        </div>
      </section>

      {/* ══ stats bar ══ */}
      <section className="scroll-reveal grid grid-cols-2 gap-4 sm:grid-cols-4">
        {STATS.map((s) => (
          <div
            key={s.label}
            className="group relative overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] px-4 py-6 text-center transition-all duration-200 hover:border-white/[0.12] hover:bg-white/[0.03]"
          >
            <div className="pointer-events-none absolute -top-12 left-1/2 h-24 w-24 -translate-x-1/2 rounded-full bg-brand-500/10 blur-2xl transition-opacity duration-200 group-hover:opacity-150 opacity-0 group-hover:opacity-100" aria-hidden />
            <div className="relative bg-gradient-to-b from-[var(--admin-text)] to-[var(--admin-text-muted)] bg-clip-text text-3xl font-bold tabular-nums text-transparent sm:text-4xl">
              {s.value}
            </div>
            <div className="relative mt-1 text-[13px] text-[var(--admin-text-dim)]">{s.label}</div>
          </div>
        ))}
      </section>

      {/* ══ features (tier 1 — featured cards with glow) ══ */}
      <section className="scroll-reveal relative overflow-hidden py-24 md:py-32">
        {/* Dot grid background */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            backgroundImage: "radial-gradient(circle, var(--admin-border) 1px, transparent 1px)",
            backgroundSize: "20px 20px",
            opacity: 0.4,
          }}
          aria-hidden
        />
        <div className="relative">
          <div className="mb-16">
            <p className="mb-4 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--admin-text-dim)]">Platform Capabilities</p>
            <h2 className="text-4xl font-bold tracking-tight text-[var(--admin-text)] md:text-5xl">
              Everything you need to
              <br />ship with confidence
            </h2>
          </div>

          {/* Tier 1: Featured cards */}
          <div className="mb-8 grid grid-cols-1 gap-6 lg:grid-cols-3">
            {tier1Features.map((feature) => (
              <div key={feature.slug} className="group relative h-full rounded-[1.25rem] border border-[var(--admin-border)] p-2 transition-transform hover:scale-[1.02] md:rounded-[1.5rem] md:p-3">
                {/* Glow ring on hover */}
                <div className="pointer-events-none absolute -inset-px rounded-[inherit] opacity-0 transition-opacity duration-300 group-hover:opacity-100" style={{ background: "radial-gradient(circle at 50% 0%, rgba(59,130,246,0.08), transparent 60%)" }} aria-hidden />
                <div className="relative flex h-full flex-col justify-between gap-6 overflow-hidden rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-8 shadow-lg shadow-black/20">
                  <div className="relative flex flex-1 flex-col justify-between gap-4">
                    <div className="w-fit rounded-lg border border-[var(--admin-border)] bg-white/[0.02] p-3">
                      <div className="[&_svg]:h-12 [&_svg]:w-12">{feature.icon}</div>
                    </div>
                    <div className="space-y-3">
                      <h3 className="text-2xl font-bold tracking-tight text-[var(--admin-text)] md:text-3xl">{feature.title}</h3>
                      <p className="text-sm leading-relaxed text-[var(--admin-text-muted)] md:text-base">{feature.description}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 text-sm font-medium text-blue-400">
                    <span>Learn more</span>
                    <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* Tier 2: Compact cards */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {tier2Features.map((feature) => (
              <div key={feature.slug} className="group h-full rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-6 transition-all hover:border-[var(--admin-border-hover)] hover:shadow-lg hover:shadow-black/20">
                <div className="flex flex-col gap-3">
                  <div className="w-fit rounded-lg border border-[var(--admin-border)] bg-white/[0.02] p-2">
                    {feature.icon}
                  </div>
                  <h3 className="text-lg font-semibold tracking-tight text-[var(--admin-text)]">{feature.title}</h3>
                  <p className="text-sm leading-relaxed text-[var(--admin-text-muted)]">{feature.description}</p>
                  <div className="mt-auto flex items-center gap-1 text-sm font-medium text-blue-400">
                    <span>Learn more</span>
                    <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ══ how it works (animated beam graph) ══ */}
      <div className="scroll-reveal"><GraphSection /></div>

      {/* ══ code example ══ */}
      <section className="scroll-reveal grid grid-cols-1 gap-8 lg:grid-cols-2 lg:gap-12">
        <div className="flex flex-col gap-5">
          <div>
            <span className="admin-label mb-3 block">Integration</span>
            <h2 className="text-3xl font-semibold tracking-[-0.01em] text-[var(--admin-text)] sm:text-4xl">
              Drop-in compatible.
              <br />Zero learning curve.
            </h2>
          </div>
          <p className="text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
            Already using OpenAI&apos;s SDK? Change one line — your base URL — and you&apos;re
            done. Works with any language or framework.
          </p>
          <ul className="space-y-2.5">
            {[
              "Works with OpenAI, Anthropic, and Vercel AI SDKs",
              "Change one line — your base URL",
              "Every request tracked with cost, latency, and token usage",
            ].map((b) => (
              <li key={b} className="flex items-start gap-2.5 text-[14px] text-[var(--admin-text-muted)]">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--admin-text-dim)]" />
                <span>{b}</span>
              </li>
            ))}
          </ul>
          <div className="mt-2 flex flex-wrap gap-3">
            <Link to="/docs" className="inline-flex h-10 items-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-5 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]">
              <BookOpen size={14} /> Read the docs
            </Link>
            <Link to="/playground" className="inline-flex h-10 items-center gap-2 rounded-[10px] px-5 text-[13px] font-medium text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]">
              <Terminal size={14} /> Try playground
            </Link>
          </div>
        </div>
        <div className="lg:sticky lg:top-24 lg:self-start">
          <div className="absolute -inset-4 rounded-3xl bg-blue-500/5 blur-2xl" aria-hidden />
          <div className="relative">
            <CodeTabs />
          </div>
        </div>
      </section>

      {/* ══ reliability / uptime ══ */}
      <section className="scroll-reveal py-20 sm:py-28">
        <div className="mx-auto max-w-5xl">
          <div className="mb-12 text-center sm:mb-16">
            <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-4 py-1.5">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
              </span>
              <span className="font-mono text-xs tracking-wider text-emerald-400">RELIABILITY</span>
            </div>
            <h2 className="mb-4 text-3xl font-bold tracking-tight text-[var(--admin-text)] sm:text-4xl lg:text-5xl">
              Never go down.{" "}
              <span className="text-[var(--admin-text-muted)]">Even when your providers do.</span>
            </h2>
            <p className="mx-auto max-w-3xl text-lg leading-relaxed text-[var(--admin-text-muted)]">
              LLM Gateway automatically routes requests to healthy providers in real-time. When one goes down,
              your traffic seamlessly fails over—your users never notice.
            </p>
          </div>

          {/* Timeline visualization */}
          <div className="rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-4 sm:p-8" role="img" aria-label="Provider uptime visualization">
            <div className="space-y-2.5 sm:space-y-3">
              {UPTIME_PROVIDERS.map((provider, index) => {
                const segments = buildSegments(provider.outages);
                const totalDown = provider.outages.reduce((sum, [s, e]) => sum + (e - s), 0);
                return (
                  <div key={provider.name} className="flex items-center gap-2 sm:gap-4">
                    <div className="w-20 shrink-0 text-right sm:w-28">
                      <span className="text-xs font-medium text-[var(--admin-text-muted)] sm:text-sm">{provider.name}</span>
                    </div>
                    <div className="relative h-5 flex-1 overflow-hidden rounded sm:h-7">
                      <div className="absolute inset-0 bg-white/[0.04]" />
                      <div className="flex h-full" style={{ clipPath: "inset(0 0 0 0)", transition: `clip-path 1.2s cubic-bezier(0.16, 1, 0.3, 1) ${index * 150}ms` }}>
                        {segments.map((seg, i) => (
                          <div key={i} className={seg.type === "up" ? "h-full bg-emerald-500/30" : "h-full bg-red-500/50"} style={{ width: `${seg.width}%` }} />
                        ))}
                      </div>
                    </div>
                    <div className="w-12 shrink-0 text-right sm:w-20">
                      <span className="font-mono text-xs text-[var(--admin-text-muted)] sm:text-sm">{100 - totalDown}%</span>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Routing indicator */}
            <div className="my-4 flex items-center gap-2 sm:my-6 sm:gap-4">
              <div className="w-20 shrink-0 sm:w-28" />
              <div className="relative flex-1 border-t border-dashed border-[var(--admin-border)]">
                <div className="absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 items-center gap-1.5 whitespace-nowrap bg-[var(--admin-surface)] px-3 py-0.5 font-mono text-xs text-[var(--admin-text-muted)]">
                  <ArrowDown className="h-3 w-3 text-blue-500" />
                  Automatic failover
                </div>
              </div>
              <div className="w-12 shrink-0 sm:w-20" />
            </div>

            {/* Gateway combined bar */}
            <div className="flex items-center gap-2 sm:gap-4">
              <div className="w-20 shrink-0 text-right sm:w-28">
                <span className="text-xs font-bold text-[var(--admin-text)] sm:text-sm">LLM Gateway</span>
              </div>
              <div className="relative h-5 flex-1 overflow-hidden rounded sm:h-7">
                <div className="absolute inset-0 bg-white/[0.04]" />
                <div className="absolute inset-0 rounded bg-emerald-500" style={{ clipPath: "inset(0 0 0 0)", transition: "clip-path 1.4s cubic-bezier(0.16, 1, 0.3, 1)", transitionDelay: "900ms" }} />
                <div className="pointer-events-none absolute inset-0 rounded" style={{ boxShadow: "0 0 20px rgba(16,185,129,0.3), inset 0 1px 0 rgba(52,211,153,0.2)", opacity: 1, transition: "opacity 0.5s ease", transitionDelay: "2s" }} />
              </div>
              <div className="w-12 shrink-0 text-right sm:w-20">
                <span className="font-mono text-xs font-bold text-emerald-400 sm:text-sm">99.9999%</span>
              </div>
            </div>
          </div>

          {/* Before/After comparison */}
          <div className="mt-8 grid gap-4 sm:mt-12 sm:grid-cols-2 sm:gap-6">
            <div className="rounded-xl border border-red-500/20 bg-red-500/[0.03] p-5 sm:p-6">
              <div className="mb-3 font-mono text-xs tracking-wider text-red-400">WITHOUT LLM GATEWAY</div>
              <div className="font-mono text-3xl font-bold sm:text-4xl text-[var(--admin-text)]">94%</div>
              <div className="mt-1 text-sm text-[var(--admin-text-muted)]">uptime per provider</div>
              <div className="mt-4 border-t border-red-500/10 pt-4">
                <div className="font-mono text-lg font-bold text-red-400">~22 days</div>
                <div className="text-sm text-[var(--admin-text-muted)]">of downtime per year</div>
              </div>
            </div>
            <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/[0.03] p-5 sm:p-6">
              <div className="mb-3 font-mono text-xs tracking-wider text-emerald-400">WITH LLM GATEWAY</div>
              <div className="font-mono text-3xl font-bold text-emerald-400 sm:text-4xl">99.9999%</div>
              <div className="mt-1 text-sm text-[var(--admin-text-muted)]">combined uptime across providers</div>
              <div className="mt-4 border-t border-emerald-500/10 pt-4">
                <div className="font-mono text-lg font-bold text-emerald-400">&lt;32 seconds</div>
                <div className="text-sm text-[var(--admin-text-muted)]">of downtime per year</div>
              </div>
            </div>
          </div>
          <p className="mx-auto mt-6 max-w-2xl text-center text-sm leading-relaxed text-[var(--admin-text-muted)]">
            Each provider averages ~94% uptime independently. With automatic failover across multiple providers,
            the probability of simultaneous downtime drops to near zero—giving you effective uptime of 99.9999%.
          </p>
        </div>
      </section>

      {/* ══ testimonials ══ */}
      <section className="scroll-reveal bg-noise relative overflow-hidden py-24 md:py-32">
        <div className="relative">
          <div className="mx-auto mb-16 max-w-7xl px-6 lg:px-8">
            <p className="mb-4 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--admin-text-dim)]">Community</p>
            <h2 className="text-4xl font-bold tracking-tight text-[var(--admin-text)] md:text-5xl">
              Trusted by developers worldwide
            </h2>
          </div>
          <div className="space-y-6">
            {/* Row 1 */}
            <div className="flex gap-6 overflow-hidden">
              <div className="animate-marquee flex shrink-0 gap-6 hover:[animation-play-state:paused]" style={{ ["--duration" as string]: "40s" }}>
                {TESTIMONIALS.slice(0, 5).map((t, i) => (
                  <div key={i} className="w-80 shrink-0 rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-5 shadow-sm">
                    <p className="text-sm leading-relaxed text-[var(--admin-text-muted)]">{t.text}</p>
                    <div className="mt-3 flex items-center gap-2">
                      <div className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-500/10 text-xs font-bold text-blue-400">{t.name[0]}</div>
                      <div>
                        <div className="text-xs font-medium text-[var(--admin-text)]">{t.name}</div>
                        <div className="text-[11px] text-[var(--admin-text-dim)]">{t.handle}</div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
              <div className="animate-marquee flex shrink-0 gap-6 hover:[animation-play-state:paused]" aria-hidden>
                {TESTIMONIALS.slice(0, 5).map((t, i) => (
                  <div key={i} className="w-80 shrink-0 rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-5 shadow-sm">
                    <p className="text-sm leading-relaxed text-[var(--admin-text-muted)]">{t.text}</p>
                    <div className="mt-3 flex items-center gap-2">
                      <div className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-500/10 text-xs font-bold text-blue-400">{t.name[0]}</div>
                      <div>
                        <div className="text-xs font-medium text-[var(--admin-text)]">{t.name}</div>
                        <div className="text-[11px] text-[var(--admin-text-dim)]">{t.handle}</div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            {/* Row 2 */}
            <div className="flex gap-6 overflow-hidden">
              <div className="animate-marquee-reverse flex shrink-0 gap-6 hover:[animation-play-state:paused]" style={{ ["--duration" as string]: "40s" }}>
                {TESTIMONIALS.slice(5).map((t, i) => (
                  <div key={i} className="w-80 shrink-0 rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-5 shadow-sm">
                    <p className="text-sm leading-relaxed text-[var(--admin-text-muted)]">{t.text}</p>
                    <div className="mt-3 flex items-center gap-2">
                      <div className="flex h-8 w-8 items-center justify-center rounded-full bg-fuchsia-500/10 text-xs font-bold text-fuchsia-400">{t.name[0]}</div>
                      <div>
                        <div className="text-xs font-medium text-[var(--admin-text)]">{t.name}</div>
                        <div className="text-[11px] text-[var(--admin-text-dim)]">{t.handle}</div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
              <div className="animate-marquee-reverse flex shrink-0 gap-6 hover:[animation-play-state:paused]" aria-hidden>
                {TESTIMONIALS.slice(5).map((t, i) => (
                  <div key={i} className="w-80 shrink-0 rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-5 shadow-sm">
                    <p className="text-sm leading-relaxed text-[var(--admin-text-muted)]">{t.text}</p>
                    <div className="mt-3 flex items-center gap-2">
                      <div className="flex h-8 w-8 items-center justify-center rounded-full bg-fuchsia-500/10 text-xs font-bold text-fuchsia-400">{t.name[0]}</div>
                      <div>
                        <div className="text-xs font-medium text-[var(--admin-text)]">{t.name}</div>
                        <div className="text-[11px] text-[var(--admin-text-dim)]">{t.handle}</div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ══ pricing ══ */}
      <section className="scroll-reveal relative py-20 md:py-28">
        <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-[var(--admin-border)] to-transparent" />
        <div className="mb-12 text-center">
          <p className="mb-4 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--admin-text-dim)]">Pricing</p>
          <h2 className="text-3xl font-bold tracking-tight text-[var(--admin-text)] md:text-4xl lg:text-5xl">
            Three ways to run it. Two are free.
          </h2>
          <p className="mx-auto mt-4 max-w-2xl text-lg text-[var(--admin-text-muted)]">
            No seats, no minimums, no token markup. Start free and only pay when you top up credits.
          </p>
        </div>
        <div className="mx-auto grid max-w-5xl grid-cols-1 gap-4 md:grid-cols-3">
          {PRICING.map((p) => {
            const Icon = p.icon;
            return (
              <div key={p.name} className="group rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-6 transition-all duration-300 hover:border-blue-500/30 hover:shadow-lg hover:shadow-blue-500/5">
                <div className={`mb-4 flex h-10 w-10 items-center justify-center rounded-lg border border-[var(--admin-border)] transition-colors group-hover:border-blue-500/30 group-hover:bg-blue-500/5 ${p.featured ? "bg-blue-500/10" : "bg-white/[0.02]"}`}>
                  <Icon className={`h-5 w-5 transition-colors group-hover:text-blue-400 ${p.featured ? "text-blue-400" : "text-[var(--admin-text-dim)]"}`} />
                </div>
                <div className="mb-1.5 flex items-baseline justify-between gap-2">
                  <h3 className="text-base font-semibold tracking-tight text-[var(--admin-text)]">{p.name}</h3>
                  <span className="font-mono text-sm font-semibold text-blue-400">{p.price}</span>
                </div>
                <p className="text-sm leading-relaxed text-[var(--admin-text-muted)]">{p.description}</p>
              </div>
            );
          })}
        </div>
        <div className="mt-8 text-center">
          <Link to="/pricing" className="inline-flex items-center gap-1.5 text-sm font-medium text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]">
            Compare all plans, including Enterprise <ArrowRight size={14} />
          </Link>
        </div>
      </section>

      {/* ══ comparison table ══ */}
      <section className="scroll-reveal">
        <div className="mb-8">
          <span className="admin-label mb-3 block">Compare</span>
          <h2 className="text-3xl font-semibold tracking-[-0.01em] text-[var(--admin-text)] sm:text-4xl">
            How wiwi compares
          </h2>
        </div>
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-left text-[13px]">
            <thead>
              <tr className="border-b border-[var(--admin-border)]">
                <th className="px-5 py-3.5 font-semibold text-[var(--admin-text)]">Feature</th>
                <th className="px-5 py-3.5 text-center font-semibold text-blue-400">wiwi</th>
                <th className="px-5 py-3.5 text-center font-medium text-[var(--admin-text-dim)]">OpenRouter</th>
                <th className="px-5 py-3.5 text-center font-medium text-[var(--admin-text-dim)]">LiteLLM</th>
                <th className="px-5 py-3.5 text-center font-medium text-[var(--admin-text-dim)]">Portkey</th>
              </tr>
            </thead>
            <tbody>
              {COMPARISON.map((row) => (
                <tr key={row.feature} className="border-b border-[var(--admin-border)] last:border-0">
                  <td className="px-5 py-3 text-[var(--admin-text-muted)]">{row.feature}</td>
                  <td className="px-5 py-3 text-center"><CheckIcon yes={row.wiwi} /></td>
                  <td className="px-5 py-3 text-center"><CheckIcon yes={row.openrouter} /></td>
                  <td className="px-5 py-3 text-center"><CheckIcon yes={row.litellm} /></td>
                  <td className="px-5 py-3 text-center"><CheckIcon yes={row.portkey} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </section>

      {/* ══ FAQ ══ */}
      <section className="scroll-reveal">
        <div className="grid grid-cols-1 gap-10 lg:grid-cols-5">
          <div className="lg:col-span-2">
            <span className="admin-label mb-3 block">FAQ</span>
            <h2 className="text-3xl font-semibold tracking-[-0.01em] text-[var(--admin-text)] sm:text-4xl">
              Common questions
            </h2>
            <p className="mt-3 text-[14px] text-[var(--admin-text-muted)]">
              Everything you need to know about the gateway, providers, and getting started.
            </p>
          </div>
          <div className="space-y-2 lg:col-span-3">
            {FAQ.map((item, i) => (
              <details key={i} className="group rounded-[12px] border border-[var(--admin-border)] bg-[var(--admin-surface)] px-5 py-4 transition-colors hover:border-[var(--admin-border-hover)]">
                <summary className="flex cursor-pointer items-center justify-between gap-4 text-[15px] font-medium text-[var(--admin-text)] marker:content-none">
                  {item.q}
                  <ChevronDown size={18} className="shrink-0 text-[var(--admin-text-dim)] transition-transform group-open:rotate-180" />
                </summary>
                <p className="mt-3 border-l-2 border-white/[0.06] pl-4 text-[14px] leading-relaxed text-[var(--admin-text-muted)]">{item.a}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      {/* ══ enterprise CTA ══ */}
      <section className="scroll-reveal relative overflow-hidden py-24 md:py-32">
        <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-[var(--admin-border)] to-transparent" />
        <div className="absolute inset-0 bg-gradient-to-b from-white/[0.03] via-white/[0.05] to-white/[0.03]" />
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_80%_50%_at_50%_-20%,rgba(59,130,246,0.08),transparent)]" />
        <div className="relative mx-auto max-w-6xl px-4">
          <div className="mb-16 text-center">
            <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-blue-500/20 bg-blue-500/5 px-4 py-1.5">
              <span className="font-mono text-xs font-medium tracking-wider text-blue-400 uppercase">Enterprise</span>
            </div>
            <h2 className="text-3xl font-bold tracking-tight text-[var(--admin-text)] md:text-4xl lg:text-5xl">
              Built for teams that
              <br />ship at scale
            </h2>
            <p className="mx-auto mt-4 max-w-2xl text-lg text-[var(--admin-text-muted)]">
              When your LLM infrastructure becomes mission-critical, you need dedicated support,
              compliance controls, and infrastructure that matches your ambitions.
            </p>
          </div>

          {/* Capability cards */}
          <div className="mb-12 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {ENTERPRISE_CAPS.map((cap) => {
              const Icon = cap.icon;
              return (
                <div key={cap.title} className="group rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-6 transition-all duration-300 hover:border-blue-500/30 hover:shadow-lg hover:shadow-blue-500/5">
                  <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg border border-[var(--admin-border)] bg-white/[0.02] transition-colors group-hover:border-blue-500/30 group-hover:bg-blue-500/5">
                    <Icon className="h-5 w-5 text-[var(--admin-text-dim)] transition-colors group-hover:text-blue-400" />
                  </div>
                  <h3 className="mb-1.5 text-base font-semibold tracking-tight text-[var(--admin-text)]">{cap.title}</h3>
                  <p className="text-sm leading-relaxed text-[var(--admin-text-muted)]">{cap.description}</p>
                </div>
              );
            })}
          </div>

          {/* CTA row */}
          <div className="flex flex-col items-center justify-center gap-4 sm:flex-row">
            <Link to="/contact" className="inline-flex h-12 items-center justify-center gap-2 rounded-[10px] bg-[var(--admin-text)] px-8 text-base font-medium text-[var(--admin-bg)] transition-opacity hover:opacity-90">
              Talk to Sales <ArrowRight size={16} />
            </Link>
            <Link to="/enterprise" className="inline-flex h-12 items-center justify-center gap-2 rounded-[10px] border border-[var(--admin-border)] bg-transparent px-8 text-base font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]">
              Explore Enterprise
            </Link>
          </div>

          {/* Trust line */}
          <div className="mt-8 flex flex-wrap items-center justify-center gap-6 text-sm text-[var(--admin-text-dim)]">
            <span>Custom SLAs</span>
            <span className="h-3 w-px bg-[var(--admin-border)]" />
            <span>Priority support</span>
            <span className="h-3 w-px bg-[var(--admin-border)]" />
            <span>SOC 2 Type II certified</span>
            <span className="hidden h-3 w-px bg-[var(--admin-border)] sm:block" />
            <span className="hidden sm:inline">On-boarding assistance</span>
          </div>
        </div>
      </section>

      {/* ══ final CTA ══ */}
      <section className="relative overflow-hidden py-32 md:py-40">
        <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-[var(--admin-border)] to-transparent" />
        <div className="absolute left-1/2 top-1/2 h-[400px] w-[600px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-blue-500/[0.06] blur-3xl" aria-hidden />
        <div className="relative mx-auto max-w-3xl text-center">
          <h2 className="text-4xl font-bold tracking-tight text-[var(--admin-text)] md:text-5xl lg:text-6xl">
            Start routing requests
            <br />in 30 seconds
          </h2>
          <p className="mx-auto mt-6 max-w-xl text-lg text-[var(--admin-text-muted)]">
            Join thousands of developers processing 100B+ tokens through LLM Gateway.
            Free tier included, no credit card required.
          </p>
          <div className="mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row">
            <Link to="/signup" className="wiwi-shimmer group inline-flex h-12 items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-8 text-base font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] duration-150 hover:brightness-110">
              Create Free Account
              <ArrowRight size={18} className="transition-transform duration-150 group-hover:translate-x-0.5" />
            </Link>
            <a href="https://github.com/theopenco/llmgateway" target="_blank" rel="noopener noreferrer" className="inline-flex h-12 items-center justify-center gap-2 rounded-[10px] border border-[var(--admin-border)] bg-transparent px-8 text-base font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]">
              Self-host LLM Gateway
            </a>
          </div>
        </div>
      </section>
    </div>
  );
}
