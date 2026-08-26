// Navbar — floating glass pill navigation bar with animated mega-dropdowns, a
// polished mobile panel, and auth controls. Matches the wiwi dark console
// language: --admin-* tokens, hairline white borders, blue/violet accents.

import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  Activity,
  Blocks,
  BookOpen,
  Bot,
  Boxes,
  Building2,
  Calculator,
  ChevronDown,
  Clock,
  Code,
  GitCompare,
  Gift,
  Github,
  Handshake,
  KeyRound,
  LayoutGrid,
  Menu,
  MessagesSquare,
  Network,
  Newspaper,
  ScrollText,
  Server,
  Shield,
  ShieldCheck,
  Sparkles,
  Trophy,
  Wrench,
  X,
  Zap,
} from "lucide-react";

const DOCS_URL = "https://docs.example.com";
const GITHUB_URL = "https://github.com";
const DISCORD_URL = "https://discord.com";

// ── dropdown link data ────────────────────────────────────────────────────

interface NavLink {
  title: string;
  href: string;
  description: string;
  icon: React.ElementType;
  gradient: string;
  external?: boolean;
}

const productsLinks: NavLink[] = [
  {
    title: "AI Gateway",
    href: "/models",
    description: "Route requests to 200+ LLMs through a single, unified API endpoint.",
    icon: Network,
    gradient: "hover:from-violet-500/20 hover:to-purple-600/30 group-hover/product:text-violet-400",
  },
  {
    title: "DevPass",
    href: "/pricing",
    description: "Fixed-price monthly plans for Claude Code, Cursor, and every coding tool.",
    icon: Code,
    gradient: "hover:from-indigo-500/20 hover:to-blue-600/30 group-hover/product:text-indigo-400",
  },
  {
    title: "Lounge",
    href: "/playground",
    description: "Every frontier model in one chat — plus image, video and audio studios.",
    icon: MessagesSquare,
    gradient: "hover:from-blue-500/20 hover:to-cyan-600/30 group-hover/product:text-blue-400",
  },
  {
    title: "Observability",
    href: "/docs",
    description: "Monitor usage, costs, and latency with real-time analytics dashboards.",
    icon: Activity,
    gradient: "hover:from-emerald-500/20 hover:to-teal-600/30 group-hover/product:text-emerald-400",
  },
];

const resourcesLinks: NavLink[] = [
  { title: "Enterprise", href: "/enterprise", description: "Custom billing, extended retention, and priority support for teams.", icon: Building2, gradient: "hover:from-blue-500/20 hover:to-blue-600/30 group-hover/product:text-blue-400" },
  { title: "Blog", href: "/changelog", description: "Product updates, tutorials, benchmarks, and announcements.", icon: Newspaper, gradient: "hover:from-amber-500/20 hover:to-orange-600/30 group-hover/product:text-amber-400" },
  { title: "Changelog", href: "/changelog", description: "What's new in wiwi across releases.", icon: ScrollText, gradient: "hover:from-violet-500/20 hover:to-purple-600/30 group-hover/product:text-violet-400" },
  { title: "Integrations", href: "/docs", description: "Connect seamlessly with popular frameworks, SDKs, and tools.", icon: Blocks, gradient: "hover:from-indigo-500/20 hover:to-blue-600/30 group-hover/product:text-indigo-400" },
  { title: "Reliability", href: "/docs", description: "Automatic failover and 99.9999% effective uptime across providers.", icon: ShieldCheck, gradient: "hover:from-emerald-500/20 hover:to-teal-600/30 group-hover/product:text-emerald-400" },
  { title: "Guardrails", href: "/docs", description: "Protect your AI with content moderation and safety filters.", icon: Shield, gradient: "hover:from-rose-500/20 hover:to-red-600/30 group-hover/product:text-rose-400" },
  { title: "Providers", href: "/models", description: "Connect and manage your provider API keys.", icon: KeyRound, gradient: "hover:from-cyan-500/20 hover:to-blue-600/30 group-hover/product:text-cyan-400" },
  { title: "Partners", href: "/about", description: "The inference partners powering the gateway.", icon: Handshake, gradient: "hover:from-teal-500/20 hover:to-emerald-600/30 group-hover/product:text-teal-400" },
  { title: "Rankings", href: "/models", description: "Top models by real token volume routed through the gateway.", icon: Trophy, gradient: "hover:from-amber-500/20 hover:to-yellow-600/30 group-hover/product:text-amber-400" },
  { title: "Apps", href: "/docs", description: "Browse apps and tools that work with wiwi.", icon: LayoutGrid, gradient: "hover:from-pink-500/20 hover:to-rose-600/30 group-hover/product:text-pink-400" },
  { title: "Models", href: "/models", description: "Browse all available LLM models and capabilities.", icon: Boxes, gradient: "hover:from-purple-500/20 hover:to-fuchsia-600/30 group-hover/product:text-purple-400" },
  { title: "Timeline", href: "/changelog", description: "Track the release history of all models.", icon: Clock, gradient: "hover:from-teal-500/20 hover:to-cyan-600/30 group-hover/product:text-teal-400" },
  { title: "Compare", href: "/compare", description: "Compare models side by side.", icon: GitCompare, gradient: "hover:from-sky-500/20 hover:to-blue-600/30 group-hover/product:text-sky-400" },
  { title: "Token Cost Calculator", href: "/pricing", description: "Calculate your LLM token costs and savings instantly.", icon: Calculator, gradient: "hover:from-green-500/20 hover:to-emerald-600/30 group-hover/product:text-green-400" },
  { title: "Referral Program", href: "/about", description: "Earn 1% of LLM spending.", icon: Gift, gradient: "hover:from-yellow-500/20 hover:to-amber-600/30 group-hover/product:text-yellow-400" },
];

const aiLinks: NavLink[] = [
  { title: "MCP Server", href: "/docs", description: "Connect AI assistants to 200+ LLMs via MCP protocol.", icon: Server, gradient: "hover:from-cyan-500/20 hover:to-blue-600/30 group-hover/product:text-cyan-400" },
  { title: "Agents", href: "/docs", description: "Pre-built AI agents with tool calling capabilities.", icon: Bot, gradient: "hover:from-violet-500/20 hover:to-purple-600/30 group-hover/product:text-violet-400" },
  { title: "AI SDK Provider", href: GITHUB_URL, description: "Use wiwi with Vercel's AI SDK.", icon: Zap, gradient: "hover:from-amber-500/20 hover:to-orange-600/30 group-hover/product:text-amber-400", external: true },
  { title: "Agent Skills", href: GITHUB_URL, description: "Skills for Claude Code and other AI agents.", icon: Sparkles, gradient: "hover:from-pink-500/20 hover:to-rose-600/30 group-hover/product:text-pink-400", external: true },
  { title: "Templates", href: "/docs", description: "Production-ready templates for AI applications.", icon: Wrench, gradient: "hover:from-emerald-500/20 hover:to-teal-600/30 group-hover/product:text-emerald-400" },
  { title: "Guides", href: "/docs", description: "Integration and usage guides for every framework.", icon: BookOpen, gradient: "hover:from-blue-500/20 hover:to-indigo-600/30 group-hover/product:text-blue-400" },
];

const mobileSections = [
  { label: "Products", items: productsLinks },
  { label: "Resources", items: resourcesLinks },
  { label: "AI", items: aiLinks },
];
