// Settings — tabbed console hub.
//
// Four sections backed by real logic where possible:
//  • General    — live gateway health polling + connection info
//  • Appearance — client-side preferences (motion, density, clock) that take
//                  effect app-wide via the prefs store + DOM classes
//  • Security   — master-key reveal / copy / clear / sign-out + clear-cache
//  • About      — version, surface count, retention notes, links
//
// Master key handling, health polling, and prefs all mirror the existing
// Dra-style dark design system.

import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Activity,
  Boxes,
  Database,
  Eye,
  EyeOff,
  Info,
  KeyRound,
  Palette,
  RefreshCw,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { clearToken, getToken } from "@/api/client";
import { api } from "@/api/client";
import { useAuth } from "@/api/auth";
import { useQueryClient } from "@tanstack/react-query";
import { useClientPrefs } from "@/lib/settings";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  CopyButton,
  PageHeader,
  Spinner,
  Toggle,
} from "@/components/ui";
import type { LucideIcon } from "lucide-react";

type Tab = "general" | "appearance" | "security" | "about";

const TABS: { id: Tab; label: string; icon: LucideIcon }[] = [
  { id: "general", label: "General", icon: Activity },
  { id: "appearance", label: "Appearance", icon: Palette },
  { id: "security", label: "Security", icon: ShieldCheck },
  { id: "about", label: "About", icon: Info },
];

// -- gateway health ----------------------------------------------------------

interface HealthResp {
  status: string;
  groups: number;
  providers: number;
}

function maskKey(key: string): string {
  if (!key) return "(not set)";
  return `${key.slice(0, 13)}…${key.slice(-4)}`;
}

function GeneralTab() {
  const [health, setHealth] = useState<HealthResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchHealth = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api<HealthResp>("/health");
      setHealth(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to reach gateway");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchHealth();
    const id = setInterval(() => void fetchHealth(), 10_000);
    return () => clearInterval(id);
  }, []);

  const ok = health?.status === "ok";

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Gateway health"
          subtitle="polled from /health every 10s"
          right={
            <Button variant="ghost" onClick={() => void fetchHealth()} disabled={loading}>
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
              Refresh
            </Button>
          }
        />
        <div className="px-5 py-4">
          {loading && !health ? (
            <div className="flex justify-center py-8">
              <Spinner />
            </div>
          ) : error ? (
            <div className="rounded-[10px] border border-red-500/10 bg-red-500/[0.04] px-3 py-2.5 text-[13px] text-red-400">
              {error}
            </div>
          ) : (
            <dl className="admin-dl">
              <dt>Status</dt>
              <dd>
                <Badge tone={ok ? "green" : "red"} title={health?.status}>
                  {ok ? "operational" : health?.status ?? "unknown"}
                </Badge>
              </dd>
              <dt>Providers</dt>
              <dd className="font-mono tabular-nums">{health?.providers ?? 0}</dd>
              <dt>Model groups</dt>
              <dd className="font-mono tabular-nums">{health?.groups ?? 0}</dd>
              <dt>Last checked</dt>
              <dd className="font-mono text-[12px] text-[var(--admin-text-muted)]">
                {new Date().toLocaleTimeString("en-US", { hour12: false })}
              </dd>
            </dl>
          )}
        </div>
      </Card>

      <Card>
        <CardHeader title="Connection" subtitle="how this browser reaches the gateway" />
        <div className="px-5 py-4">
          <dl className="admin-dl">
            <dt>API base</dt>
            <dd className="font-mono text-[12px]">
              {window.location.origin}
              <span className="text-[var(--admin-text-dim)]">/admin</span>
            </dd>
            <dt>Admin surface</dt>
            <dd>
              <Badge tone="violet">/admin/*</Badge>
              <span className="ml-2 text-[12px] text-[var(--admin-text-muted)]">
                master key required
              </span>
            </dd>
            <dt>Proxied surfaces</dt>
            <dd className="flex flex-wrap gap-2">
              <Badge tone="blue">OpenAI Chat</Badge>
              <Badge tone="blue">OpenAI Responses</Badge>
              <Badge tone="blue">Anthropic Messages</Badge>
            </dd>
          </dl>
        </div>
      </Card>
    </div>
  );
}

// -- appearance --------------------------------------------------------------

function AppearanceTab() {
  const { prefs, update, reset } = useClientPrefs();
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Motion"
          subtitle="animation and transition behavior across the console"
        />
        <div className="px-5 py-4">
          <div className="admin-setting-row">
            <div>
              <p className="admin-setting-row-title">Reduce motion</p>
              <p className="admin-setting-row-desc">
                Disables entrance animations, pulses, and shimmer effects.
              </p>
            </div>
            <Toggle checked={prefs.reduceMotion} onChange={(v) => update({ reduceMotion: v })} />
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Density"
          subtitle="table and list padding throughout the console"
        />
        <div className="px-5 py-4">
          <div className="admin-setting-row">
            <div>
              <p className="admin-setting-row-title">Compact tables</p>
              <p className="admin-setting-row-desc">
                Tightens row padding in tables and log views to fit more rows.
              </p>
            </div>
            <Toggle
              checked={prefs.density === "compact"}
              onChange={(v) => update({ density: v ? "compact" : "comfortable" })}
            />
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader title="Topbar clock" subtitle="time format shown in the topbar" />
        <div className="px-5 py-4">
          <div className="admin-setting-row">
            <div>
              <p className="admin-setting-row-title">24-hour clock</p>
              <p className="admin-setting-row-desc">
                Uses 24-hour time when on, 12-hour am/pm when off.
              </p>
            </div>
            <Toggle checked={prefs.clock24h} onChange={(v) => update({ clock24h: v })} />
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader title="Theme" subtitle="the console uses the fixed dark theme" />
        <div className="flex items-center gap-3 px-5 py-4">
          <Badge tone="violet">dark</Badge>
          <span className="text-[13px] text-[var(--admin-text-muted)]">
            Light mode is not yet available.
          </span>
        </div>
      </Card>

      <div className="flex justify-end">
        <Button variant="ghost" onClick={reset}>
          <RefreshCw size={14} />
          Reset to defaults
        </Button>
      </div>
    </div>
  );
}

// -- security ---------------------------------------------------------------

function SecurityTab() {
  const [revealed, setRevealed] = useState(false);
  const [copied, setCopied] = useState(false);
  const [cacheCleared, setCacheCleared] = useState(false);
  const navigate = useNavigate();
  const { logout } = useAuth();
  const qc = useQueryClient();
  const token = getToken();

  function clearCache() {
    // Drop all cached server data (logs, overview, timeseries, pricing,
    // providers, keys) from the in-memory React Query cache so every page
    // refetches fresh data on next visit. This is where the stats/log data
    qc.clear();
    setCacheCleared(true);
    setTimeout(() => setCacheCleared(false), 1500);
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Master key"
          subtitle="stored in this browser's localStorage and sent as a bearer token"
          right={
            token && (
              <CopyButton
                text={token}
              />
            )
          }
        />
        <div className="px-5 py-4">
          <div className="flex items-center gap-3">
            <code className="flex-1 rounded-[10px] border border-[var(--admin-border)] bg-white/[0.02] px-3 py-2.5 font-mono text-[13px] text-[var(--admin-text)]">
              {revealed ? token || "(not set)" : maskKey(token)}
            </code>
            <Button
              variant="ghost"
              onClick={() => setRevealed((r) => !r)}
              title={revealed ? "Hide key" : "Reveal key"}
            >
              {revealed ? <EyeOff size={14} /> : <Eye size={14} />}
              {revealed ? "Hide" : "Reveal"}
            </Button>
          </div>
          <p className="mt-3 text-[11px] leading-relaxed text-[var(--admin-text-dim)]">
            The master key authenticates every <span className="font-mono">/admin/*</span> request
            and is stored unencrypted in this browser. Avoid sharing it; clear it on shared
            machines.
          </p>
        </div>
      </Card>

      <Card>
        <CardHeader title="Session" subtitle="sign out or rotate the stored key" />
        <div className="space-y-3 px-5 py-4">
          <div className="admin-setting-row">
            <div>
              <p className="admin-setting-row-title">Sign out</p>
              <p className="admin-setting-row-desc">
                Clears the stored master key and returns to the login screen.
              </p>
            </div>
            <Button
              variant="danger"
              onClick={() => {
                logout();
                navigate("/login");
              }}
            >
              Sign out
            </Button>
          </div>
          <div className="admin-setting-row">
            <div>
              <p className="admin-setting-row-title">Clear key only</p>
              <p className="admin-setting-row-desc">
                Removes the stored key without navigating away.
              </p>
            </div>
            <Button
              variant="ghost"
              onClick={() => {
                clearToken();
                setRevealed(false);
                setCopied(true);
                setTimeout(() => setCopied(false), 1200);
              }}
            >
              <KeyRound size={14} />
              {copied ? "Cleared" : "Clear key"}
            </Button>
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader title="Local data" subtitle="clear cached console state" />
        <div className="px-5 py-4">
          <div className="admin-setting-row">
            <div>
              <p className="admin-setting-row-title">Clear local cache</p>
              <p className="admin-setting-row-desc">
                Drops all cached server data (logs, usage stats, pricing) so every page
                refetches fresh data on next visit. Your master key and preferences are kept.
              </p>
            </div>
            <Button variant="ghost" onClick={clearCache}>
              <Trash2 size={14} />
              {cacheCleared ? "Cleared" : "Clear cache"}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

// -- about ------------------------------------------------------------------

function AboutTab() {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Build" subtitle="version and runtime info" />
        <div className="px-5 py-4">
          <dl className="admin-dl">
            <dt>Version</dt>
            <dd className="font-mono">v0.1.0</dd>
            <dt>Edition</dt>
            <dd>unified LLM gateway proxy</dd>
            <dt>Theme</dt>
            <dd>
              <Badge tone="violet">dark</Badge>
            </dd>
          </dl>
        </div>
      </Card>

      <Card>
        <CardHeader title="Architecture" subtitle="hub-and-spoke translation" />
        <div className="px-5 py-4">
          <dl className="admin-dl">
            <dt>Inbound surfaces</dt>
            <dd className="flex flex-wrap gap-2">
              <Badge tone="blue">OpenAI Chat</Badge>
              <Badge tone="blue">OpenAI Responses</Badge>
              <Badge tone="blue">Anthropic Messages</Badge>
            </dd>
            <dt>Translation</dt>
            <dd className="text-[var(--admin-text-muted)]">dialect → IR → provider</dd>
            <dt>Encoding</dt>
            <dd className="text-[var(--admin-text-muted)]">responses re-encoded per caller</dd>
          </dl>
        </div>
      </Card>

      <Card>
        <CardHeader title="Data retention" subtitle="how long request and proxy data lives" />
        <div className="space-y-1.5 px-5 py-4 text-[13px] text-[var(--admin-text-muted)]">
          <p>Request and proxy log rings hold ~500 events in memory.</p>
          <p>Stats windows are computed from that ring.</p>
          <p>DB persistence lands post-MVP.</p>
        </div>
      </Card>

      <Card>
        <CardHeader title="Resources" subtitle="reference material" />
        <div className="px-5 py-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <InfoTile icon={Database} title="Models" desc="configure deployments" to="/models" />
            <InfoTile icon={Boxes} title="Providers" desc="outbound accounts" to="/providers" />
            <InfoTile icon={KeyRound} title="Virtual keys" desc="client keys" to="/keys" />
          </div>
        </div>
      </Card>
    </div>
  );
}

function InfoTile(props: { icon: LucideIcon; title: string; desc: string; to: string }) {
  const Icon = props.icon;
  return (
    <Link
      to={props.to}
      className="group flex items-center gap-3 rounded-[12px] border border-[var(--admin-border)] bg-white/[0.015] px-4 py-3 transition-colors hover:border-[var(--admin-border-hover)] hover:bg-white/[0.025]"
    >
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] bg-gradient-to-br from-blue-500/15 to-violet-500/15 ring-1 ring-white/[0.06]">
        <Icon className="h-4 w-4" style={{ color: "rgba(59,130,246,0.7)" }} />
      </span>
      <div className="min-w-0">
        <p className="text-[13px] font-medium text-[var(--admin-text)]">{props.title}</p>
        <p className="truncate text-[11px] text-[var(--admin-text-dim)]">{props.desc}</p>
      </div>
    </Link>
  );
}

// -- page -------------------------------------------------------------------

export function SettingsPage() {
  const [tab, setTab] = useState<Tab>("general");

  return (
    <div>
      <PageHeader
        title="Settings"
        subtitle="console preferences, security, and gateway info"
      />

      <div className="mb-5 flex gap-2 overflow-x-auto">
        <div className="admin-tabs">
          {TABS.map((t) => {
            const Icon = t.icon;
            return (
              <button
                key={t.id}
                className="admin-tab"
                data-active={tab === t.id}
                onClick={() => setTab(t.id)}
              >
                <Icon className="h-3.5 w-3.5" />
                {t.label}
              </button>
            );
          })}
        </div>
      </div>

      {tab === "general" && <GeneralTab />}
      {tab === "appearance" && <AppearanceTab />}
      {tab === "security" && <SecurityTab />}
      {tab === "about" && <AboutTab />}
    </div>
  );
}
