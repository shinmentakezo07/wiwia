// Settings — stored-key info and informational panels about log retention
// and the gateway itself. The console theme is fixed dark (Dra-style system).

import { getToken } from "@/api/client";
import { Badge, Card, CardHeader, PageHeader } from "@/components/ui";

/** First 13 chars + ellipsis + last 4, or a placeholder when unset. */
function maskKey(key: string): string {
  if (!key) return "(not set)";
  return `${key.slice(0, 13)}…${key.slice(-4)}`;
}

export function SettingsPage() {
  return (
    <div>
      <PageHeader title="Settings" subtitle="client preferences and gateway info" />

      <div className="grid max-w-2xl gap-4">
        <Card>
          <CardHeader title="Master key" />
          <div className="px-5 py-4">
            <p className="font-mono text-[13px] text-[var(--admin-text)]">
              {maskKey(getToken())}
            </p>
            <p className="mt-1.5 font-mono text-[11px] text-[var(--admin-text-dim)]">
              stored in this browser&apos;s localStorage
            </p>
          </div>
        </Card>

        <Card>
          <CardHeader title="Retention" />
          <div className="space-y-1.5 px-5 py-4 text-[13px] text-[var(--admin-text-muted)]">
            <p>Request and proxy log rings hold ~500 events in memory.</p>
            <p>Stats windows are computed from that ring.</p>
            <p>DB persistence lands post-MVP.</p>
          </div>
        </Card>

        <Card>
          <CardHeader title="Appearance" />
          <div className="flex items-center gap-3 px-5 py-4">
            <Badge tone="violet">dark</Badge>
            <span className="text-[13px] text-[var(--admin-text-muted)]">
              The console uses the fixed dark theme.
            </span>
          </div>
        </Card>

        <Card>
          <CardHeader title="About" />
          <div className="px-5 py-4 text-[13px] text-[var(--admin-text-muted)]">
            wiwi <span className="font-medium text-[var(--admin-text)]">v0.1.0</span> — unified LLM
            gateway proxy
          </div>
        </Card>
      </div>
    </div>
  );
}
