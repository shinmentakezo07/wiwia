// Contact — form plus a sidebar with contact channels. Matches the dark
// design system shared with the admin console.

import { useState } from "react";
import { Link } from "react-router-dom";
import { BookOpen, Github, Mail, Send } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button, Card, Input } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const CHANNELS: { icon: LucideIcon; label: string; value: string; href: string; external?: boolean }[] = [
  {
    icon: Mail,
    label: "Email",
    value: "hello@wiwi.dev",
    href: "mailto:hello@wiwi.dev",
  },
  {
    icon: Github,
    label: "GitHub",
    value: "github.com/wiwi",
    href: "https://github.com/shinmentakezo07/wiwia",
    external: true,
  },
  {
    icon: BookOpen,
    label: "Documentation",
    value: "Read the docs",
    href: "/docs",
  },
];

export function ContactPage() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [sent, setSent] = useState(false);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSent(true);
  }

  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="text-center">
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          Get in{" "}
          <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
            touch
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          Questions about self-hosting, routing, or provider support? Pick whichever
          channel suits you — email gets a reply from a human, usually within one
          business day.
        </p>
      </section>

      {/* ── form + sidebar ── */}
      <section className="grid grid-cols-1 gap-4 md:grid-cols-[1fr_280px]">
        {/* form */}
        <Card className="p-6">
          <h2 className="mb-1 text-[16px] font-semibold text-[var(--admin-text)]">Send a message</h2>
          <p className="mb-5 text-[12px] text-[var(--admin-text-dim)]">
            We read everything. Fields marked with{" "}
            <span className="text-blue-400">*</span> are required.
          </p>
          {sent ? (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-emerald-500/10 ring-1 ring-emerald-500/20">
                <Send size={20} className="text-emerald-400" />
              </div>
              <h3 className="mt-4 text-[16px] font-semibold text-[var(--admin-text)]">Message sent</h3>
              <p className="mt-1.5 text-[13px] text-[var(--admin-text-muted)]">
                Thanks for reaching out. We will get back to you shortly.
              </p>
              <Button
                variant="outline"
                className="mt-5"
                onClick={() => {
                  setSent(false);
                  setName("");
                  setEmail("");
                  setMessage("");
                }}
              >
                Send another
              </Button>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <label className="block">
                <span className="admin-label mb-1.5 block">Name <span className="text-blue-400">*</span></span>
                <Input
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Your name"
                />
              </label>
              <label className="block">
                <span className="admin-label mb-1.5 block">Email <span className="text-blue-400">*</span></span>
                <Input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                />
              </label>
              <label className="block">
                <span className="admin-label mb-1.5 block">Message <span className="text-blue-400">*</span></span>
                <textarea
                  required
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  rows={5}
                  placeholder="How can we help?"
                  className="admin-input w-full resize-none"
                />
              </label>
              <Button type="submit" className="w-full">
                <Send size={14} /> Send message
              </Button>
            </form>
          )}
        </Card>

        {/* sidebar */}
        <div className="space-y-4">
          <Card className="p-5">
            <span className="admin-label mb-3 block">Contact channels</span>
            <div className="space-y-3">
              {CHANNELS.map((c) => {
                const Icon = c.icon;
                return (
                  <div key={c.label} className="flex items-start gap-3">
                    <Icon className="mt-0.5 h-4 w-4 shrink-0 text-[var(--admin-text-dim)]" />
                    <div className="min-w-0">
                      <p className="text-[12px] text-[var(--admin-text-dim)]">{c.label}</p>
                      {c.external ? (
                        <a
                          href={c.href}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-[13px] font-medium text-blue-400 transition-colors hover:text-blue-300"
                        >
                          {c.value}
                        </a>
                      ) : (
                        <Link
                          to={c.href}
                          className="text-[13px] font-medium text-blue-400 transition-colors hover:text-blue-300"
                        >
                          {c.value}
                        </Link>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>
          <Card className="p-5">
            <span className="admin-label mb-2 block">Response time</span>
            <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
              We typically reply within one business day. For urgent production issues,
              open an issue on{" "}
              <a
                href="https://github.com/shinmentakezo07/wiwia"
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-400 transition-colors hover:text-blue-300"
              >
                GitHub
              </a>
              .
            </p>
            <div className="mt-3 flex items-center gap-1.5 font-mono text-[10px] text-[var(--admin-text-dim)]">
              <Mail size={10} />
              <code style={{ fontFamily: MONO }}>hello@wiwi.dev</code>
            </div>
          </Card>
        </div>
      </section>
    </div>
  );
}
