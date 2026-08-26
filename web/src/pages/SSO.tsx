// SSO — sign in with single sign-on. Adapted from the llmgateway.io SSO page,
// simplified to a static page in the dark design system.

import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, ArrowRight, Building2, Loader2 } from "lucide-react";
import { Button, Card, Input } from "@/components/ui";

export function SSOPage() {
  const [email, setEmail] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setIsLoading(true);
    setTimeout(() => setIsLoading(false), 2000);
  }

  return (
    <div className="mx-auto w-full max-w-[400px] space-y-6 pb-16">
      {/* ── header ── */}
      <div className="space-y-2">
        <h1 className="text-[24px] font-bold tracking-tight text-[var(--admin-text)] sm:text-[28px]">
          Sign in with SSO
        </h1>
        <p className="text-[14px] text-[var(--admin-text-muted)]">
          Enter your work email and we&apos;ll redirect you to your organization&apos;s
          identity provider.
        </p>
      </div>

      {/* ── form ── */}
      <div className="space-y-4">
        <Card className="p-5">
          <form onSubmit={onSubmit} className="space-y-4">
            <label className="block">
              <span className="admin-label mb-1.5 block">Email</span>
              <Input
                type="email"
                placeholder="name@example.com"
                autoComplete="username"
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </label>
            <Button type="submit" className="w-full" disabled={isLoading}>
              {isLoading ? (
                <>
                  <Loader2 size={16} className="mr-2 animate-spin" />
                  Redirecting...
                </>
              ) : (
                <>
                  <Building2 size={16} className="mr-2" />
                  Continue with SSO
                  <ArrowRight size={16} className="ml-2" />
                </>
              )}
            </Button>
          </form>
        </Card>

        <Link
          to="/login"
          className="inline-flex w-full items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-4 py-2.5 text-[14px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
        >
          <ArrowLeft size={16} className="mr-2" />
          Back to login
        </Link>
      </div>
    </div>
  );
}
