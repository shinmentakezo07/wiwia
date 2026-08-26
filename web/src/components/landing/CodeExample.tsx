// CodeExample — tabbed code examples with copy-to-clipboard. Replaces shiki
// (heavy async highlighter) with a lightweight regex-based highlighter that
// returns styled spans inline. Replaces framer-motion AnimatedGroup with CSS
// AnimatedGroup, next-themes with a hardcoded dark theme, and radix toast
// with a simple inline "Copied" state.

import { Check, Copy } from "lucide-react";
import { Fragment, useState, type CSSProperties } from "react";
import { AnimatedGroup } from "./AnimatedGroup";

interface CodeExample {
  label: string;
  language: string;
  code: string;
}

const codeExamples: Record<string, CodeExample> = {
  curl: {
    label: "cURL",
    language: "bash",
    code: `curl -X POST https://api.wiwi.io/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer $WIWI_API_KEY" \\
  -d '{
  "model": "gpt-4o",
  "messages": [
    {"role": "user", "content": "Hello, how are you?"}
  ]
}'`,
  },
  typescript: {
    label: "TypeScript",
    language: "typescript",
    code: `import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.WIWI_API_KEY,
  baseURL: "https://api.wiwi.io/v1/"
});

const response = await client.chat.completions.create({
  model: "gpt-4o",
  messages: [
    { role: "user", content: "Hello, how are you?" }
  ]
});

console.log(response.choices[0].message.content);`,
  },
  python: {
    label: "Python",
    language: "python",
    code: `import openai

client = openai.OpenAI(
    api_key="YOUR_WIWI_API_KEY",
    base_url="https://api.wiwi.io/v1"
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello, how are you?"}]
)
print(response.choices[0].message.content)
`,
  },
  go: {
    label: "Go",
    language: "go",
    code: `package main

import (
    "context"
    "fmt"
    "os"

    openai "github.com/sashabaranov/go-openai"
)

func main() {
    client := openai.NewClient(os.Getenv("WIWI_API_KEY"))
    resp, err := client.CreateChatCompletion(
        context.Background(),
        openai.ChatCompletionRequest{
            Model: "gpt-4o",
            Messages: []openai.ChatCompletionMessage{
                {Role: openai.ChatMessageRoleUser, Content: "Hello, how are you?"},
            },
        },
    )
    if err != nil {
        panic(err)
    }
    fmt.Println(resp.Choices[0].Message.Content)
}
`,
  },
  ruby: {
    label: "Ruby",
    language: "ruby",
    code: `require "openai"

client = OpenAI::Client.new(
  access_token: ENV["WIWI_API_KEY"],
  uri_base: "https://api.wiwi.io/v1"
)

response = client.chat(
  parameters: {
    model: "gpt-4o",
    messages: [{ role: "user", content: "Hello, how are you?" }]
  }
)

puts response.dig("choices", 0, "message", "content")
`,
  },
};

const bullets = [
  "Works with OpenAI, Anthropic, and Vercel AI SDKs",
  "Change one line — your base URL",
  "Every request tracked with cost, latency, and token usage",
];

// ── Lightweight syntax highlighting ──────────────────────────────────────
// Returns an array of {text, color} tokens. Supports a few common languages
// well enough for a landing-page code block. Not a full tokenizer — just
// string/comment/keyword/number coloring layered on top.

interface Token {
  text: string;
  color?: string;
  bold?: boolean;
}

const KEYWORDS: Record<string, RegExp> = {
  typescript: /\b(import|from|const|let|var|async|await|new|export|default|return|if|else|for|while|class|interface|type|function|public|private|readonly|void|string|number|boolean|true|false|null|undefined)\b/g,
  python: /\b(import|from|def|class|return|if|elif|else|for|while|try|except|with|as|async|await|True|False|None|print|self|lambda|yield|raise|in|not|and|or|is|pass|break|continue|global|nonlocal)\b/g,
  go: /\b(package|import|func|var|const|type|struct|interface|return|if|else|for|range|go|defer|chan|map|make|new|nil|true|false|string|int|int64|float64|bool|error|context|fmt|os|main)\b/g,
  ruby: /\b(require|module|class|def|end|do|if|elsif|else|unless|while|until|return|puts|new|attr|true|false|nil|self|lambda|yield|raise|begin|rescue|ensure|then|and|or|not)\b/g,
  bash: /\b(echo|export|local|if|then|fi|for|do|done|while|case|esac|function|return|cd|curl|wget|npm|node|python|ruby|go)\b/g,
};

function highlight(code: string, language: string): Token[] {
  const tokens: Token[] = [];
  const kwRe = KEYWORDS[language];
  // We tokenize by scanning for strings, comments, then everything else.
  let i = 0;
  const n = code.length;

  const isStringStart = (ch: string) => ch === '"' || ch === "'" || ch === "`";

  while (i < n) {
    const ch = code[i];

    // Comments
    if (ch === "#" || (ch === "/" && code[i + 1] === "/")) {
      let end = code.indexOf("\n", i);
      if (end === -1) end = n;
      tokens.push({ text: code.slice(i, end), color: "#6a9955" });
      i = end;
      continue;
    }
    if (language === "bash" && ch === "#" ) {
      let end = code.indexOf("\n", i);
      if (end === -1) end = n;
      tokens.push({ text: code.slice(i, end), color: "#6a9955" });
      i = end;
      continue;
    }

    // Strings
    if (isStringStart(ch)) {
      const quote = ch;
      let j = i + 1;
      while (j < n && code[j] !== quote) {
        if (code[j] === "\\") j++; // skip escaped
        j++;
      }
      j++; // include closing quote
      tokens.push({ text: code.slice(i, Math.min(j, n)), color: "#ce9178" });
      i = Math.min(j, n);
      continue;
    }

    // Numbers
    if (/[0-9]/.test(ch)) {
      let j = i;
      while (j < n && /[0-9.]/.test(code[j])) j++;
      tokens.push({ text: code.slice(i, j), color: "#b5cea8" });
      i = j;
      continue;
    }

    // Keywords (try match at current position)
    if (kwRe && /\w/.test(ch)) {
      let j = i;
      while (j < n && /\w/.test(code[j])) j++;
      const word = code.slice(i, j);
      // Check if word is a keyword (use the regex against the standalone word)
      kwRe.lastIndex = 0;
      if (kwRe.test(word)) {
        tokens.push({ text: word, color: "#569cd6", bold: true });
      } else {
        // Function call heuristic: word followed by (
        let k = j;
        while (k < n && code[k] === " ") k++;
        if (code[k] === "(") {
          tokens.push({ text: word, color: "#dcdcaa" });
        } else {
          tokens.push({ text: word });
        }
      }
      i = j;
      continue;
    }

    // Default: accumulate non-word chars
    let j = i;
    while (j < n && !/\w/.test(code[j]) && !isStringStart(code[j]) && code[j] !== "#") {
      j++;
    }
    if (j === i) j++; // ensure progress
    tokens.push({ text: code.slice(i, j), color: "#d4d4d4" });
    i = j;
  }

  return tokens;
}

function HighlightedCode({ code, language }: { code: string; language: string }) {
  const lines = code.split("\n");
  return (
    <code>
      {lines.map((line, lineIdx) => {
        const lineTokens = highlight(line, language);
        return (
          <Fragment key={lineIdx}>
            {lineTokens.map((tok, tokIdx) => (
              <span
                key={tokIdx}
                style={{
                  color: tok.color,
                  fontWeight: tok.bold ? "bold" : undefined,
                } as CSSProperties}
              >
                {tok.text}
              </span>
            ))}
            {lineIdx < lines.length - 1 ? "\n" : null}
          </Fragment>
        );
      })}
    </code>
  );
}

export function CodeExample() {
  const [activeTab, setActiveTab] = useState<string>("python");
  const [copied, setCopied] = useState(false);

  const currentExample = codeExamples[activeTab];

  const copyToClipboard = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore
    }
  };

  return (
    <section className="py-24 md:py-32">
      <div className="container mx-auto px-4">
        <div className="grid grid-cols-1 items-start gap-12 lg:grid-cols-2 lg:gap-16">
          {/* Left column: heading, description, bullets, tabs */}
          <AnimatedGroup preset="blur-slide" className="flex flex-col gap-6">
            <div>
              <p className="mb-4 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--admin-text-muted)]">
                Integration
              </p>
              <h2 className="text-4xl font-bold tracking-tight text-[var(--admin-text)] md:text-5xl">
                Drop-in compatible.
                <br />
                Zero learning curve.
              </h2>
            </div>

            <p className="text-lg text-[var(--admin-text-muted)]">
              Already using OpenAI's SDK? Change one line — your base URL — and you're
              done. Works with any language or framework.
            </p>

            <ul className="space-y-3">
              {bullets.map((bullet) => (
                <li
                  key={bullet}
                  className="flex items-start gap-3 text-[var(--admin-text-muted)]"
                >
                  <div className="mt-1.5 size-1.5 shrink-0 rounded-full bg-[var(--admin-text)]/40" />
                  <span>{bullet}</span>
                </li>
              ))}
            </ul>

            {/* Vertical language tabs (desktop) */}
            <div className="mt-4 hidden flex-col gap-1 lg:flex">
              {Object.entries(codeExamples).map(([key, example]) => (
                <button
                  key={key}
                  onClick={() => setActiveTab(key)}
                  className={`rounded-lg px-4 py-2 text-left text-sm font-medium transition-colors ${
                    activeTab === key
                      ? "bg-[var(--admin-text)] text-[var(--admin-bg)]"
                      : "text-[var(--admin-text-muted)] hover:bg-white/[0.04]"
                  }`}
                >
                  {example.label}
                </button>
              ))}
            </div>
          </AnimatedGroup>

          {/* Right column: code block (sticky on desktop) */}
          <div className="relative lg:sticky lg:top-24">
            {/* Faint glow behind code block */}
            <div className="absolute -inset-4 rounded-3xl bg-blue-500/5 blur-2xl pointer-events-none" />

            {/* Horizontal tabs (mobile) */}
            <div className="mb-4 lg:hidden">
              <div className="flex flex-wrap gap-2">
                {Object.entries(codeExamples).map(([key, example]) => (
                  <button
                    key={key}
                    onClick={() => setActiveTab(key)}
                    className={`rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                      activeTab === key
                        ? "bg-[var(--admin-text)] text-[var(--admin-bg)]"
                        : "text-[var(--admin-text-muted)] hover:bg-white/[0.04]"
                    }`}
                  >
                    {example.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="relative overflow-hidden rounded-2xl border border-[var(--admin-border)] shadow-2xl">
              {/* macOS-style header */}
              <div className="flex items-center justify-between border-b border-[var(--admin-border)] bg-white/[0.02] px-4 py-3 backdrop-blur-sm">
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-2">
                    <div className="size-3 rounded-full bg-[#FF5F57]" />
                    <div className="size-3 rounded-full bg-[#FEBC2E]" />
                    <div className="size-3 rounded-full bg-[#28C840]" />
                  </div>
                  <span className="ml-2 text-sm font-medium text-[var(--admin-text-muted)]">
                    {currentExample.label}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => copyToClipboard(currentExample.code)}
                  className="flex h-8 items-center gap-1 rounded-md px-2 text-sm text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]"
                >
                  {copied ? <Check className="mr-1 size-4" /> : <Copy className="mr-1 size-4" />}
                  {copied ? "Copied" : "Copy"}
                </button>
              </div>

              <div className="relative bg-[var(--admin-bg)]">
                <pre className="max-h-96 overflow-auto p-6 font-mono text-sm leading-relaxed">
                  <HighlightedCode code={currentExample.code} language={currentExample.language} />
                </pre>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
