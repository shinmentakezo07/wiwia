// Markdown renderer for the docs section — dependency-free (no markdown
// parser package). Consumes block tokens from markdown-utils.ts and renders
// them with the shared dark design system: anchored headings, highlighted
// code blocks with copy buttons, styled tables, lists, quotes, and inline
// emphasis/links. Cross-document .md links resolve through the registry.

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Check, Copy } from "lucide-react";
import {
  DOC_MONO,
  highlight,
  langFromLabel,
  parseBlocks,
  type Lang,
  type MdListItem,
  type Token,
} from "./markdown-utils";

// ── copy buttons ───────────────────────────────────────────────────────────

function CopyBtn(props: { text: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return (
    <button
      type="button"
      onClick={async () => {
        await navigator.clipboard.writeText(props.text);
        setCopied(true);
        timer.current = setTimeout(() => setCopied(false), 1500);
      }}
      className="absolute right-2.5 top-2.5 flex items-center gap-1 rounded-md border border-white/[0.06] bg-white/[0.02] px-2 py-1 text-[10px] font-medium text-[var(--admin-text-dim)] opacity-0 transition-all hover:text-[var(--admin-text)] group-hover:opacity-100 focus-visible:opacity-100"
      aria-label="Copy code"
    >
      {copied ? <Check size={11} /> : <Copy size={11} />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

// Small, always-visible copy button for endpoint paths / one-liners.
export function PathCopyBtn(props: { text: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return (
    <button
      type="button"
      onClick={async () => {
        await navigator.clipboard.writeText(props.text);
        setCopied(true);
        timer.current = setTimeout(() => setCopied(false), 1500);
      }}
      className="rounded-md border border-white/[0.06] bg-white/[0.02] p-1 text-[var(--admin-text-dim)] transition-all hover:border-white/[0.12] hover:text-blue-300"
      aria-label={`Copy ${props.text}`}
    >
      {copied ? <Check size={11} /> : <Copy size={11} />}
    </button>
  );
}

// ── code block ─────────────────────────────────────────────────────────────

function TokenSpan(props: { tokens: Token[] }) {
  const { tokens } = props;
  return (
    <>
      {tokens.map((t, i) =>
        typeof t === "string" ? (
          t
        ) : (
          <span key={i} className={t.cls}>
            {t.text}
          </span>
        ),
      )}
    </>
  );
}

export function CodeBlock(props: { code: string; label?: string; lang?: Lang }) {
  const lang = props.lang ?? (props.label ? langFromLabel(props.label) : "bash");
  const tokens = useMemo(() => highlight(props.code, lang), [props.code, lang]);
  return (
    <div className="docs-codeblock group relative overflow-hidden rounded-[10px] border border-[var(--admin-border)] bg-[var(--admin-surface)]">
      <div className="docs-codeblock-glow" aria-hidden />
      {props.label && (
        <div className="relative z-10 flex items-center justify-between border-b border-[var(--admin-border)] bg-white/[0.015] px-3.5 py-1.5">
          <div className="flex items-center gap-1.5 pl-0.5">
            <span className="h-2 w-2 rounded-full bg-[#ff5f57]/70" aria-hidden />
            <span className="h-2 w-2 rounded-full bg-[#febc2e]/70" aria-hidden />
            <span className="h-2 w-2 rounded-full bg-[#28c840]/70" aria-hidden />
            <span className="admin-label ml-1.5 text-[10px]">{props.label}</span>
          </div>
          <CopyBtn text={props.code} />
        </div>
      )}
      {!props.label && <CopyBtn text={props.code} />}
      <pre className="relative z-10 overflow-x-auto px-3.5 py-3 text-[12px] leading-relaxed">
        <code className="text-[var(--admin-text-muted)]" style={{ fontFamily: DOC_MONO }}>
          <TokenSpan tokens={tokens} />
        </code>
      </pre>
    </div>
  );
}

// ── inline markdown ────────────────────────────────────────────────────────

// Ordered alternation: code → bold → italic → link → bare URL.
const INLINE_RE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)|(\[[^\]\n]+\]\([^)\s]+\))|(https?:\/\/[^\s<>)]+)/g;

function smoothTo(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function Inline(props: {
  text: string;
  resolveHref?: (href: string) => string | null;
  keyPrefix: string;
}) {
  const { text, resolveHref, keyPrefix } = props;
  const nodes: ReactNode[] = [];
  let last = 0;
  let k = 0;
  for (const m of text.matchAll(INLINE_RE)) {
    const idx = m.index ?? 0;
    if (idx > last) nodes.push(text.slice(last, idx));
    const raw = m[0];
    const key = `${keyPrefix}-${k++}`;
    if (raw.startsWith("`")) {
      nodes.push(
        <code key={key} className="mdx-code" style={{ fontFamily: DOC_MONO }}>
          {raw.slice(1, -1)}
        </code>,
      );
    } else if (raw.startsWith("**")) {
      // Bold may wrap code spans — render its contents recursively.
      nodes.push(
        <strong key={key} className="mdx-strong">
          <Inline text={raw.slice(2, -2)} resolveHref={resolveHref} keyPrefix={key} />
        </strong>,
      );
    } else if (raw.startsWith("*")) {
      nodes.push(<em key={key}>{raw.slice(1, -1)}</em>);
    } else if (raw.startsWith("[")) {
      const mm = raw.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/);
      if (mm) {
        const label = mm[1] ?? "";
        const href = mm[2] ?? "";
        if (href.startsWith("#")) {
          nodes.push(
            <a
              key={key}
              className="mdx-a"
              href={href}
              onClick={(e) => {
                e.preventDefault();
                smoothTo(href.slice(1));
              }}
            >
              {label}
            </a>,
          );
        } else {
          const internal = resolveHref?.(href) ?? null;
          if (internal) {
            nodes.push(
              <Link key={key} className="mdx-a" to={internal}>
                {label}
              </Link>,
            );
          } else {
            nodes.push(
              <a key={key} className="mdx-a" href={href} target="_blank" rel="noreferrer">
                {label}
              </a>,
            );
          }
        }
      } else {
        nodes.push(raw);
      }
    } else {
      nodes.push(
        <a key={key} className="mdx-a" href={raw} target="_blank" rel="noreferrer">
          {raw}
        </a>,
      );
    }
    last = idx + raw.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return <>{nodes}</>;
}

// ── block renderers ────────────────────────────────────────────────────────

function ListItem(props: { item: MdListItem; resolveHref?: (href: string) => string | null; k: string }) {
  const { item, resolveHref, k } = props;
  return (
    <li className="mdx-li" style={item.depth > 0 ? { marginLeft: item.depth * 18 } : undefined}>
      {item.checked !== null && (
        <span className={`mdx-check${item.checked ? " is-done" : ""}`} aria-hidden>
          {item.checked ? <Check size={10} strokeWidth={3} /> : null}
        </span>
      )}
      <Inline text={item.text} resolveHref={resolveHref} keyPrefix={k} />
    </li>
  );
}

function MdHeading(props: {
  depth: number;
  text: string;
  id: string;
  resolveHref?: (href: string) => string | null;
}) {
  const { depth, text, id, resolveHref } = props;
  const Tag = (depth <= 2 ? "h2" : depth === 3 ? "h3" : "h4") as "h2" | "h3" | "h4";
  return (
    <Tag id={id} className={depth === 2 ? "mdx-h2" : depth === 3 ? "mdx-h3" : "mdx-h4"}>
      <Inline text={text} resolveHref={resolveHref} keyPrefix={`h-${id}`} />
      <a
        href={`#${id}`}
        className="mdx-anchor"
        aria-label="Link to this section"
        onClick={(e) => {
          e.preventDefault();
          smoothTo(id);
        }}
      >
        #
      </a>
    </Tag>
  );
}

// ── document ───────────────────────────────────────────────────────────────

export function MarkdownDoc(props: {
  md: string;
  resolveHref?: (href: string) => string | null;
}) {
  const blocks = useMemo(() => parseBlocks(props.md), [props.md]);
  const { resolveHref } = props;
  return (
    <div className="mdx">
      {blocks.map((b, i) => {
        switch (b.kind) {
          case "heading":
            return (
              <MdHeading
                key={i}
                depth={b.depth}
                text={b.text}
                id={b.id}
                resolveHref={resolveHref}
              />
            );
          case "p":
            return (
              <p key={i} className="mdx-p">
                <Inline text={b.text} resolveHref={resolveHref} keyPrefix={`p${i}`} />
              </p>
            );
          case "code":
            return (
              <div key={i} className="mdx-codeblock">
                <CodeBlock code={b.code} label={b.info || undefined} lang={b.lang} />
              </div>
            );
          case "list":
            return b.ordered ? (
              <ol key={i} className="mdx-ol">
                {b.items.map((it, j) => (
                  <ListItem key={j} item={it} resolveHref={resolveHref} k={`l${i}-${j}`} />
                ))}
              </ol>
            ) : (
              <ul key={i} className="mdx-ul">
                {b.items.map((it, j) => (
                  <ListItem key={j} item={it} resolveHref={resolveHref} k={`l${i}-${j}`} />
                ))}
              </ul>
            );
          case "table":
            return (
              <div key={i} className="mdx-tablewrap">
                <table className="mdx-table">
                  <thead>
                    <tr>
                      {b.header.map((c, j) => (
                        <th key={j}>
                          <Inline text={c} resolveHref={resolveHref} keyPrefix={`th${i}-${j}`} />
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {b.rows.map((r, ri) => (
                      <tr key={ri}>
                        {r.map((c, ci) => (
                          <td key={ci}>
                            <Inline text={c} resolveHref={resolveHref} keyPrefix={`td${i}-${ri}-${ci}`} />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "quote":
            return (
              <blockquote key={i} className="mdx-quote">
                {b.lines.map((l, j) => (
                  <p key={j} className="mdx-quote-line">
                    <Inline text={l} resolveHref={resolveHref} keyPrefix={`q${i}-${j}`} />
                  </p>
                ))}
              </blockquote>
            );
          case "hr":
            return <hr key={i} className="mdx-hr" />;
        }
      })}
    </div>
  );
}
