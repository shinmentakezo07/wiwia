// Pure markdown utilities for the docs section: block tokenizer, heading
// slugs, TOC extraction, and dependency-free syntax highlighting. Deliberately
// React-free — JSX rendering lives in markdown.tsx. The 13 files in /docs use
// headings, fenced code, tables, lists (incl. `- [ ]` checkboxes), quotes,
// and inline emphasis/links only; this tokenizer covers exactly that subset.

export const DOC_MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

// ── syntax highlighting ────────────────────────────────────────────────────

export type Lang = "bash" | "python" | "yaml" | "text";

// Ordered alternation — earlier groups win at the same position.
const LANG_REGEX: Record<Exclude<Lang, "text">, RegExp> = {
  bash: /(?<com>#.*$)|(?<url>https?:\/\/[^\s"']+)|(?<str>"[^"\n]*"|'[^'\n]*')|(?<var>\$\{?[A-Za-z_][A-Za-z0-9_]*\}?)|(?<flag>\s--?[A-Za-z][\w-]*)|(?<cmd>^export\s|^curl\b)/gm,
  python: /(?<com>#.*$)|(?<str>f?"[^"\n]*"|f?'[^'\n]*')|(?<kw>\b(?:from|import|print|def|return|class|True|False|None)\b)|(?<num>\b\d+\b)/gm,
  yaml: /(?<com>(?:^|\s)#.*$)|(?<key>^[ \t]*-?[ \t]*[\w.-]+(?=:))|(?<str>"[^"\n]*"|'[^'\n]*')|(?<bool>\b(?:true|false)\b)|(?<num>\b\d+(?:\.\d+)?\b)/gm,
};

const TOKEN_CLASS: Record<string, string> = {
  com: "tok-com",
  str: "tok-str",
  var: "tok-var",
  flag: "tok-flag",
  cmd: "tok-kw",
  kw: "tok-kw",
  key: "tok-key",
  num: "tok-num",
  bool: "tok-bool",
  url: "tok-url",
};

/** A plain-text run (string) or a highlighted token (class + text). */
export type Token = string | { cls: string; text: string };

export function highlight(code: string, lang: Lang): Token[] {
  if (lang === "text") return [code];
  const out: Token[] = [];
  let last = 0;
  for (const m of code.matchAll(LANG_REGEX[lang])) {
    const idx = m.index ?? 0;
    if (idx > last) out.push(code.slice(last, idx));
    const groups = m.groups ?? {};
    const name = Object.keys(groups).find((g) => groups[g] !== undefined);
    const cls = name ? TOKEN_CLASS[name] : undefined;
    out.push(cls ? { cls, text: m[0] } : m[0]);
    last = idx + m[0].length;
  }
  if (last < code.length) out.push(code.slice(last));
  return out;
}

/** Language from a fenced-code info string (```yaml …). Unknown → plain. */
export function langFromFence(info: string): Lang {
  const l = info.trim().toLowerCase();
  if (l === "python" || l === "py") return "python";
  if (l === "yaml" || l === "yml") return "yaml";
  if (l === "bash" || l === "sh" || l === "shell" || l === "console") return "bash";
  return "text";
}

/** Language from a human label ("curl", "wiwi.yaml") — bash by default. */
export function langFromLabel(label: string): Lang {
  const l = label.toLowerCase();
  if (l.includes("python")) return "python";
  if (l.includes("yaml") || l.includes("yml")) return "yaml";
  return "bash";
}

// ── heading slugs ──────────────────────────────────────────────────────────

export function slugifyHeading(text: string): string {
  const base = text
    .toLowerCase()
    .replace(/[`*_[\]()#]/g, "")
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return base || "section";
}

/** Strip inline markdown markers (code, bold, links) to plain text. */
export function plainText(md: string): string {
  return md
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
}

/** Remove the first `# Title` line — the article header renders it instead. */
export function stripLeadingH1(md: string): string {
  const m = md.match(/^\s*#\s+[^\n]*\n?/);
  return m ? md.slice(m[0].length) : md;
}

// ── block tokenizer ────────────────────────────────────────────────────────

export interface MdListItem {
  text: string;
  /** null = plain bullet; true/false = `- [x]` / `- [ ]` checkbox. */
  checked: boolean | null;
  depth: number;
}

export type MdBlock =
  | { kind: "code"; lang: Lang; code: string; info: string }
  | { kind: "heading"; depth: number; text: string; id: string }
  | { kind: "p"; text: string }
  | { kind: "list"; ordered: boolean; items: MdListItem[] }
  | { kind: "table"; header: string[]; rows: string[][] }
  | { kind: "quote"; lines: string[] }
  | { kind: "hr" };

const FENCE_RE = /^```(.*)$/;
const FENCE_CLOSE_RE = /^```\s*$/;
const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const UL_RE = /^(\s*)[-*+]\s+(.*)$/;
const OL_RE = /^(\s*)\d+[.)]\s+(.*)$/;
const CHECK_RE = /^\[([ xX])\]\s+(.*)$/;
const HR_RE = /^(?:-{3,}|\*{3,}|_{3,})$/;
const TABLE_SEP_RE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const BQ_RE = /^>\s?(.*)$/;

/** Split a table row on `|`, ignoring pipes inside inline code spans. */
function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  const cells: string[] = [];
  let cur = "";
  let inCode = false;
  for (const ch of s) {
    if (ch === "`") inCode = !inCode;
    if (ch === "|" && !inCode) {
      cells.push(cur.trim());
      cur = "";
    } else {
      cur += ch;
    }
  }
  cells.push(cur.trim());
  return cells;
}

function pushItem(items: MdListItem[], indent: number, raw: string): void {
  const chk = raw.match(CHECK_RE);
  items.push({
    text: (chk ? (chk[2] ?? "") : raw).trim(),
    checked: chk ? (chk[1]?.toLowerCase() === "x") : null,
    depth: Math.min(2, Math.floor(indent / 2)),
  });
}

export function parseBlocks(md: string): MdBlock[] {
  const lines = md.replace(/\r\n/g, "\n").split("\n");
  const blocks: MdBlock[] = [];
  const seenIds = new Set<string>();
  let i = 0;
  let para: string[] = [];

  const flushPara = () => {
    if (para.length) {
      blocks.push({ kind: "p", text: para.join(" ").trim() });
      para = [];
    }
  };

  while (i < lines.length) {
    const line = lines[i] ?? "";

    if (!line.trim()) {
      flushPara();
      i++;
      continue;
    }

    const fence = line.match(FENCE_RE);
    if (fence) {
      flushPara();
      const info = (fence[1] ?? "").trim();
      const body: string[] = [];
      i++;
      while (i < lines.length && !FENCE_CLOSE_RE.test(lines[i] ?? "")) {
        body.push(lines[i] ?? "");
        i++;
      }
      i++; // skip the closing fence (or EOF)
      blocks.push({ kind: "code", lang: langFromFence(info), code: body.join("\n"), info });
      continue;
    }

    const heading = line.match(HEADING_RE);
    if (heading) {
      flushPara();
      const text = (heading[2] ?? "").trim();
      let id = slugifyHeading(text);
      let n = 2;
      while (seenIds.has(id)) id = `${slugifyHeading(text)}-${n++}`;
      seenIds.add(id);
      blocks.push({ kind: "heading", depth: heading[1]?.length ?? 2, text, id });
      i++;
      continue;
    }

    if (HR_RE.test(line.trim())) {
      flushPara();
      blocks.push({ kind: "hr" });
      i++;
      continue;
    }

    // Table: header row containing | followed by a --- separator row.
    if (line.includes("|") && i + 1 < lines.length && TABLE_SEP_RE.test(lines[i + 1] ?? "")) {
      flushPara();
      const header = splitRow(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length) {
        const l = lines[i] ?? "";
        if (!l.includes("|") || !l.trim()) break;
        rows.push(splitRow(l));
        i++;
      }
      blocks.push({ kind: "table", header, rows });
      continue;
    }

    const bq = line.match(BQ_RE);
    if (bq) {
      flushPara();
      const body: string[] = [bq[1] ?? ""];
      i++;
      while (i < lines.length) {
        const m = (lines[i] ?? "").match(BQ_RE);
        if (!m) break;
        body.push(m[1] ?? "");
        i++;
      }
      blocks.push({ kind: "quote", lines: body });
      continue;
    }

    const ul = line.match(UL_RE);
    const ol = line.match(OL_RE);
    if (ul || ol) {
      flushPara();
      const ordered = Boolean(ol && !ul);
      const items: MdListItem[] = [];
      while (i < lines.length) {
        const l = lines[i] ?? "";
        const mu = l.match(UL_RE);
        const mo = l.match(OL_RE);
        if (mu && !ordered) {
          pushItem(items, mu[1]?.length ?? 0, mu[2] ?? "");
          i++;
        } else if (mo && ordered) {
          pushItem(items, mo[1]?.length ?? 0, mo[2] ?? "");
          i++;
        } else if (/^\s{2,}\S/.test(l) && items.length > 0 && !FENCE_RE.test(l)) {
          // Indented continuation line — append to the previous item.
          const prev = items[items.length - 1];
          if (prev) prev.text += ` ${l.trim()}`;
          i++;
        } else {
          break;
        }
      }
      blocks.push({ kind: "list", ordered, items });
      continue;
    }

    para.push(line.trim());
    i++;
  }
  flushPara();
  return blocks;
}

// ── table of contents ──────────────────────────────────────────────────────

export interface TocEntry {
  id: string;
  text: string;
  depth: 2 | 3;
}

export function parseToc(md: string): TocEntry[] {
  return parseBlocks(md)
    .filter(
      (b): b is Extract<MdBlock, { kind: "heading" }> =>
        b.kind === "heading" && (b.depth === 2 || b.depth === 3),
    )
    .map((h) => ({ id: h.id, text: plainText(h.text), depth: h.depth === 2 ? 2 : 3 }));
}
