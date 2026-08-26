// Playground — authenticated chat playground for trying model groups with a
// virtual key. Key picker + inline "Create key", model picker (from /admin/models
// group names), message list + composer. Sends to /v1/chat/completions with a
// plain fetch (NOT the `api()` wrapper) so we can set the virtual-key bearer —
// `api()` hardcodes the master bearer, which is wrong here.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus, Send, Sparkles } from "lucide-react";
import { generateKey, getModels, listKeys } from "@/api/client";
import type { VirtualKey } from "@/api/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorText,
  Field,
  Input,
  PageHeader,
  Select,
  Spinner,
} from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

type Msg = { role: "user" | "assistant"; content: string };

function fmtTokens(n: number | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString();
}

export function PlaygroundPage() {
  const qc = useQueryClient();
  const keysQ = useQuery({ queryKey: ["keys"], queryFn: listKeys, refetchInterval: 30_000 });
  const modelsQ = useQuery({ queryKey: ["models"], queryFn: getModels });

  // plaintext bearer for the selected key — kept in component state because the
  // list endpoint only returns masked secrets; generated keys carry the plaintext
  // for the chosen id.
  const [bearer, setBearer] = useState<string>("");
  const [keyId, setKeyId] = useState<string>("");
  const [model, setModel] = useState<string>("");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [usage, setUsage] = useState<{ prompt_tokens: number; completion_tokens: number; total_tokens: number } | null>(null);

  const keys: VirtualKey[] = keysQ.data?.keys ?? [];
  const groups = modelsQ.data?.groups ?? [];

  // Derive the effective model rather than storing a defaulted one in state.
  // This avoids a state-update-during-render (setModel in the render body)
  // when the groups query resolves. `model` is "" until the user picks one,
  // so we fall back to the first group's name for display + the request body.
  const effectiveModel = model || groups[0]?.name || "";

  // create-key mutation
  const createKey = useMutation({
    mutationFn: () => generateKey({ name: "playground" }),
    onSuccess: (data) => {
      setBearer(data.key);
      setKeyId(data.id);
      void qc.invalidateQueries({ queryKey: ["keys"] });
    },
    onError: (e) => setErr(e.message),
  });

  function chooseKey(k: VirtualKey) {
    setKeyId(k.id);
    // We only have the plaintext for a freshly-generated key; for a pre-existing
    // key the list endpoint returns masked secrets, so the caller must paste the
    // bearer manually via the field below.
    setBearer("");
  }

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    if (!bearer.trim()) {
      setErr("Pick a key (and paste its plaintext bearer) — the list endpoint only returns masked secrets.");
      return;
    }
    if (!effectiveModel) {
      setErr("Pick a model group.");
      return;
    }
    setErr(null);
    setBusy(true);
    setUsage(null);
    const next: Msg[] = [...messages, { role: "user", content: text }];
    setMessages(next);
    setDraft("");
    try {
      const resp = await fetch("/v1/chat/completions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${bearer.trim()}`,
        },
        body: JSON.stringify({ model: effectiveModel, messages: next, stream: false }),
      });
      const body = await resp.json().catch(() => null);
      if (!resp.ok) {
        const msg =
          (body as { error?: { message?: string } | string } | null)?.error &&
          typeof (body as { error: { message?: string } }).error === "object"
            ? (body as { error: { message?: string } }).error.message
            : (body as { error?: string } | null)?.error ?? `HTTP ${resp.status}`;
        throw new Error(msg ?? `HTTP ${resp.status}`);
      }
      const content = (body as { choices?: { message?: { content?: string } }[] }).choices?.[0]?.message?.content ?? "(empty response)";
      setMessages([...next, { role: "assistant", content }]);
      const u = (body as { usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number } }).usage;
      if (u) setUsage(u);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "request failed");
    } finally {
      setBusy(false);
    }
  }

  const noKeys = keysQ.isSuccess && keys.length === 0;

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        title="Playground"
        subtitle="Send a chat completion through wiwi with one of your virtual keys."
      />

      {/* setup row */}
      <Card className="mb-4 p-4">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-[1fr_1fr_auto]">
          <Field label="Virtual key">
            <div className="flex items-center gap-2">
              <Select
                value={keyId}
                onChange={(v) => {
                  const k = keys.find((x) => x.id === v);
                  if (k) chooseKey(k);
                }}
                options={[
                  { value: "", label: noKeys ? "no keys — create one →" : "select a key…" },
                  ...keys.map((k) => ({
                    value: k.id,
                    label: `${k.alias}${k.disabled ? " (disabled)" : ""}`,
                  })),
                ]}
                className="flex-1"
              />
              <Button variant="outline" onClick={() => createKey.mutate()} disabled={createKey.isPending}>
                <Plus size={14} /> Create key
              </Button>
            </div>
          </Field>
          <Field label="Model group">
            <Select
              value={effectiveModel}
              onChange={setModel}
              options={[
                { value: "", label: groups.length ? "select a model…" : "loading…" },
                ...groups.map((g) => ({ value: g.name, label: g.name })),
              ]}
            />
          </Field>
          <Field label="Bearer (plaintext)">
            <Input
              type="password"
              value={bearer}
              onChange={(e) => setBearer(e.target.value)}
              placeholder="sk-wiwi-…"
              className="font-mono"
            />
          </Field>
        </div>
        {createKey.data && (
          <p className="mt-2 text-[11px] text-emerald-400/80">
            Created key <code style={{ fontFamily: MONO }}>{createKey.data.id.slice(0, 8)}</code> — its plaintext
            bearer was filled in above. Copy it from the Virtual Keys page if you lose it.
          </p>
        )}
      </Card>

      {err && (
        <div className="mb-3">
          <ErrorText>{err}</ErrorText>
        </div>
      )}

      {/* messages */}
      <Card className="mb-4">
        <div className="max-h-[420px] min-h-[260px] overflow-y-auto p-4">
          {messages.length === 0 ? (
            <EmptyState>
              <Sparkles size={18} className="mx-auto mb-2 opacity-40" />
              Send a message to try a model group.
            </EmptyState>
          ) : (
            <div className="space-y-3">
              {messages.map((m, i) => (
                <div
                  key={i}
                  className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
                >
                  <div
                    className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-[13px] leading-relaxed ${
                      m.role === "user"
                        ? "bg-blue-500/15 text-[var(--admin-text)]"
                        : "bg-white/[0.03] text-[var(--admin-text)]"
                    }`}
                  >
                    <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-[var(--admin-text-dim)]">
                      {m.role}
                    </p>
                    <p className="whitespace-pre-wrap">{m.content}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>

      {/* usage */}
      {usage && (
        <div className="mb-3 flex flex-wrap gap-2">
          <Badge tone="blue">in {fmtTokens(usage.prompt_tokens)}</Badge>
          <Badge tone="violet">out {fmtTokens(usage.completion_tokens)}</Badge>
          <Badge tone="gray">total {fmtTokens(usage.total_tokens)}</Badge>
        </div>
      )}

      {/* composer */}
      <form onSubmit={send} className="flex items-end gap-2">
        <div className="flex-1">
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={noKeys ? "Create a key to start chatting…" : "Message the model…"}
            disabled={busy}
            className="h-11"
          />
        </div>
        <Button type="submit" disabled={busy || !draft.trim()}>
          {busy ? <Spinner className="h-4 w-4" /> : <Send size={14} />}
          Send
        </Button>
      </form>

      {noKeys && (
        <p className="mt-3 text-[12px] text-[var(--admin-text-dim)]">
          <KeyRound size={12} className="mr-1 inline" />
          You have no virtual keys yet. Use <strong>Create key</strong> above to mint one for the playground.
        </p>
      )}
    </div>
  );
}
