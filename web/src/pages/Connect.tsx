// Connect — CLI connection authorization flow. Adapted from the llmgateway.io
// connect/cli page, simplified to a static authorization card in the dark design system.

import { Link } from "react-router-dom";
import { CheckCircle2, ShieldCheck, Terminal } from "lucide-react";
import { Card } from "@/components/ui";

const CLI_KEY_TTL_DAYS = 90;

export function ConnectPage() {
  return (
    <div className="mx-auto w-full max-w-md space-y-6 pb-16">
      {/* ── authorize card ── */}
      <Card className="overflow-hidden">
        <div className="border-b border-[var(--admin-border)] px-5 py-4">
          <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-blue-500/10">
            <Terminal className="h-5 w-5 text-blue-400" />
          </div>
          <h1 className="text-[16px] font-semibold text-[var(--admin-text)]">
            Authorize your CLI
          </h1>
          <p className="mt-1 text-[13px] text-[var(--admin-text-muted)]">
            Your coding agent wants to connect to your gateway account. Approving will
            create an API key and send it back to the tool running in your terminal.
          </p>
        </div>
        <div className="space-y-3 px-5 py-4 text-[13px]">
          <div className="flex items-start gap-2 text-[var(--admin-text-muted)]">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-blue-400" />
            <span>
              Sign in to your gateway account to connect your tool to your terminal.
            </span>
          </div>
          <p className="text-[12px] text-[var(--admin-text-dim)]">
            The key is delivered only to a local address on this machine, expires in{" "}
            {CLI_KEY_TTL_DAYS} days, and can be revoked any time from the API Keys page.
          </p>
        </div>
        <div className="flex flex-col gap-2 px-5 pb-5">
          <Link
            to="/login"
            className="inline-flex w-full items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-4 py-2.5 text-[14px] font-medium text-white transition-[filter] hover:brightness-110"
          >
            Sign in to continue
          </Link>
        </div>
      </Card>

      {/* ── success state preview ── */}
      <Card className="overflow-hidden">
        <div className="border-b border-[var(--admin-border)] px-5 py-4">
          <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-emerald-500/10">
            <CheckCircle2 className="h-5 w-5 text-emerald-400" />
          </div>
          <h2 className="text-[16px] font-semibold text-[var(--admin-text)]">You&apos;re connected</h2>
          <p className="mt-1 text-[13px] text-[var(--admin-text-muted)]">
            Your coding agent has been authorized. You can close this tab and return to
            your terminal.
          </p>
        </div>
        <div className="px-5 py-4">
          <div className="flex items-start gap-2 text-[12px] text-[var(--admin-text-dim)]">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-blue-400" />
            <span>
              The freshly minted CLI key expires in {CLI_KEY_TTL_DAYS} days and can be
              revoked from the API Keys page at any time.
            </span>
          </div>
        </div>
      </Card>
    </div>
  );
}
