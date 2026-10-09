// Dependency-free, line-safe syntax coloring for the docs family
// (Docs, Guides, Migration, and the shared detail pages). Tokenizing is a
// single ordered alternation per language, so a match never spans a newline
// and the output is always a flat list of spans — safe to render inside any
// <pre> without a highlighter dependency.
//
// Owned here (not inside a page) so the docs page and the shared detail-page
// chrome speak one code convention instead of two.

import type { ReactNode } from "react";

export const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

export type Lang = "bash" | "python" | "yaml";

// Ordered alternation — earlier groups win at the same position.
export const LANG_REGEX: Record<Lang, RegExp> = {
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

export function highlight(code: string, lang: Lang): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let k = 0;
  for (const m of code.matchAll(LANG_REGEX[lang])) {
    const idx = m.index ?? 0;
    if (idx > last) out.push(code.slice(last, idx));
    const groups = m.groups ?? {};
    const name = Object.keys(groups).find((g) => groups[g] !== undefined);
    out.push(
      <span key={k++} className={name ? TOKEN_CLASS[name] : undefined}>
        {m[0]}
      </span>,
    );
    last = idx + m[0].length;
  }
  if (last < code.length) out.push(code.slice(last));
  return out;
}

export function langFromLabel(label: string): Lang {
  const l = label.toLowerCase();
  if (l.includes("python")) return "python";
  if (l.includes("yaml") || l.includes("yml")) return "yaml";
  return "bash";
}

// Language identity dot for the code-block header: bash/curl emerald, python
// amber, yaml violet — the same hue family the syntax tokens already speak.
export const LANG_DOT: Record<Lang, string> = {
  bash: "bg-emerald-400",
  python: "bg-amber-400",
  yaml: "bg-violet-400",
};
