// Minimal, dependency-free Markdown renderer for assistant chat messages.
//
// Builds React nodes directly — never uses dangerouslySetInnerHTML, so it is
// safe by construction against model-emitted HTML. Supports the subset that
// LLMs actually produce in chat: fenced code blocks (with language label +
// copy), inline code, bold/italic, headings, ordered/unordered lists (one
// nesting level), blockquotes, GFM pipe tables, links, and horizontal rules.
//
// Streaming-safe: an unterminated code fence or table simply renders as far
// as the content goes; nothing throws on partial input.

import { useMemo, useState } from "react";
import { Check, Copy } from "lucide-react";

// ── inline parsing ─────────────────────────────────────────────────────────

/** Inline-token pattern. Kept as a string so each call can build a fresh
 * `/g` regex: the old module-level regex shared `lastIndex` across the
 * recursion in `parseInline`, so a nested bold/italic call reset it and the
 * outer loop re-matched the same token forever (frozen main thread). */
const INLINE_PATTERN =
  "(`[^`\\n]+`)|(\\*\\*[^*\\n]+\\*\\*)|(__[^_\\n]+__)|(\\*[^*\\n]+\\*)|(_[^_\\n]+_)|(\\[[^\\]\\n]+\\]\\([^)\\s]+\\))";

function parseInline(text: string, keyBase: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  const re = new RegExp(INLINE_PATTERN, "g");
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const tok = m[0];
    const key = `${keyBase}-i${i++}`;
    if (m[1]) {
      nodes.push(
        <code key={key} className="pg-md-code">
          {tok.slice(1, -1)}
        </code>,
      );
    } else if (m[2] || m[3]) {
      const inner = tok.slice(2, -2);
      nodes.push(<strong key={key}>{parseInline(inner, key)}</strong>);
    } else if (m[4] || m[5]) {
      const inner = tok.slice(1, -1);
      nodes.push(<em key={key}>{parseInline(inner, key)}</em>);
    } else if (m[6]) {
      const bracket = tok.indexOf("]");
      const label = tok.slice(1, bracket);
      const href = tok.slice(bracket + 2, -1);
      nodes.push(
        <a key={key} href={href} target="_blank" rel="noopener noreferrer" className="pg-md-link">
          {label}
        </a>,
      );
    }
    last = m.index + tok.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

// ── block parsing ──────────────────────────────────────────────────────────

type Block =
  | { kind: "code"; lang: string; content: string }
  | { kind: "heading"; level: number; text: string }
  | { kind: "quote"; lines: string[] }
  | { kind: "ul"; items: { text: string; children: string[] }[] }
  | { kind: "ol"; items: { text: string; children: string[] }[] }
  | { kind: "table"; head: string[]; rows: string[][] }
  | { kind: "hr" }
  | { kind: "p"; text: string };

function splitRow(line: string): string[] {
  return line
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map((c) => c.trim());
}

function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i]!;

    // fenced code block — consume until closing fence or EOF (streaming-safe)
    const fence = line.match(/^\s*```(\w*)\s*$/);
    if (fence) {
      const lang = fence[1] || "";
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i]!)) {
        body.push(lines[i]!);
        i++;
      }
      i++; // skip closing fence (no-op at EOF)
      blocks.push({ kind: "code", lang, content: body.join("\n") });
      continue;
    }

    // horizontal rule
    if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) {
      blocks.push({ kind: "hr" });
      i++;
      continue;
    }

    // heading
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1]!.length, text: heading[2]! });
      i++;
      continue;
    }

    // blockquote (contiguous)
    if (/^\s*>\s?/.test(line)) {
      const body: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i]!)) {
        body.push(lines[i]!.replace(/^\s*>\s?/, ""));
        i++;
      }
      blocks.push({ kind: "quote", lines: body });
      continue;
    }

    // table: header row + separator row
    if (
      line.includes("|") &&
      i + 1 < lines.length &&
      /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1]!) &&
      lines[i + 1]!.includes("-")
    ) {
      const head = splitRow(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i]!.includes("|") && lines[i]!.trim() !== "") {
        rows.push(splitRow(lines[i]!));
        i++;
      }
      blocks.push({ kind: "table", head, rows });
      continue;
    }

    // unordered list
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: { text: string; children: string[] }[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i]!)) {
        const text = lines[i]!.replace(/^\s*[-*+]\s+/, "");
        const children: string[] = [];
        i++;
        while (i < lines.length && /^\s{2,}[-*+]\s+/.test(lines[i]!)) {
          children.push(lines[i]!.replace(/^\s*[-*+]\s+/, ""));
          i++;
        }
        items.push({ text, children });
      }
      blocks.push({ kind: "ul", items });
      continue;
    }

    // ordered list
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: { text: string; children: string[] }[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i]!)) {
        const text = lines[i]!.replace(/^\s*\d+[.)]\s+/, "");
        const children: string[] = [];
        i++;
        while (i < lines.length && /^\s{2,}\d+[.)]\s+/.test(lines[i]!)) {
          children.push(lines[i]!.replace(/^\s*\d+[.)]\s+/, ""));
          i++;
        }
        items.push({ text, children });
      }
      blocks.push({ kind: "ol", items });
      continue;
    }

    // blank line
    if (line.trim() === "") {
      i++;
      continue;
    }

    // paragraph — gather contiguous non-blank, non-special lines
    const para: string[] = [line];
    i++;
    while (
      i < lines.length &&
      lines[i]!.trim() !== "" &&
      !/^\s*```/.test(lines[i]!) &&
      !/^(#{1,4})\s+/.test(lines[i]!) &&
      !/^\s*>\s?/.test(lines[i]!) &&
      !/^\s*[-*+]\s+/.test(lines[i]!) &&
      !/^\s*\d+[.)]\s+/.test(lines[i]!)
    ) {
      para.push(lines[i]!);
      i++;
    }
    blocks.push({ kind: "p", text: para.join("\n") });
  }

  return blocks;
}

// ── code block with copy ───────────────────────────────────────────────────

function CodeBlock({ lang, content }: { lang: string; content: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="pg-code">
      <div className="pg-code-head">
        <span className="pg-code-lang">{lang || "text"}</span>
        <button type="button" onClick={copy} className="pg-code-copy" aria-label="Copy code">
          {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="pg-code-pre">
        <code>{content}</code>
      </pre>
    </div>
  );
}

// ── renderer ───────────────────────────────────────────────────────────────

export function Markdown({ content, caret }: { content: string; caret?: boolean }) {
  const blocks = useMemo(() => parseBlocks(content), [content]);

  return (
    <div className="pg-md">
      {blocks.map((b, idx) => {
        const key = `b${idx}`;
        switch (b.kind) {
          case "code":
            return <CodeBlock key={key} lang={b.lang} content={b.content} />;
          case "heading": {
            const Tag = (`h${Math.min(b.level + 2, 6)}`) as "h3" | "h4" | "h5" | "h6";
            return (
              <Tag key={key} className={`pg-md-h pg-md-h${b.level}`}>
                {parseInline(b.text, key)}
              </Tag>
            );
          }
          case "quote":
            return (
              <blockquote key={key} className="pg-md-quote">
                {b.lines.map((l, j) => (
                  <p key={j} className="pg-md-quote-p">
                    {parseInline(l, `${key}-${j}`)}
                  </p>
                ))}
              </blockquote>
            );
          case "ul":
            return (
              <ul key={key} className="pg-md-ul">
                {b.items.map((it, j) => (
                  <li key={j}>
                    {parseInline(it.text, `${key}-${j}`)}
                    {it.children.length > 0 && (
                      <ul className="pg-md-ul pg-md-ul-nested">
                        {it.children.map((c, k) => (
                          <li key={k}>{parseInline(c, `${key}-${j}-${k}`)}</li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            );
          case "ol":
            return (
              <ol key={key} className="pg-md-ol">
                {b.items.map((it, j) => (
                  <li key={j}>
                    {parseInline(it.text, `${key}-${j}`)}
                    {it.children.length > 0 && (
                      <ol className="pg-md-ol pg-md-ol-nested">
                        {it.children.map((c, k) => (
                          <li key={k}>{parseInline(c, `${key}-${j}-${k}`)}</li>
                        ))}
                      </ol>
                    )}
                  </li>
                ))}
              </ol>
            );
          case "table":
            return (
              <div key={key} className="pg-md-tablewrap">
                <table className="pg-md-table">
                  <thead>
                    <tr>
                      {b.head.map((h, j) => (
                        <th key={j}>{parseInline(h, `${key}-h${j}`)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {b.rows.map((r, j) => (
                      <tr key={j}>
                        {r.map((c, k) => (
                          <td key={k}>{parseInline(c, `${key}-${j}-${k}`)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "hr":
            return <hr key={key} className="pg-md-hr" />;
          case "p":
          default: {
            const paraLines = b.text.split("\n");
            return (
              <p key={key} className="pg-md-p">
                {paraLines.map((l, j) => (
                  <span key={j}>
                    {parseInline(l, `${key}-${j}`)}
                    {j < paraLines.length - 1 && <br />}
                  </span>
                ))}
              </p>
            );
          }
        }
      })}
      {caret && <span className="pg-caret pg-md-caret" aria-hidden />}
    </div>
  );
}
