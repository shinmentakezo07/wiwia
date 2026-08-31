// GithubStars — client-side fetch of the GitHub star count for the repo.
// Replaces the Next.js server component + getConfig with a direct fetch.
// Falls back to a star glyph if the API call fails.

import { useEffect, useState } from "react";
import { Github, Star } from "lucide-react";

const REPO = "shinmentakezo07/wiwia";

function formatNumber(num: number | null): string {
  if (num === null) return "\u2605";
  if (num >= 1_000_000) return (num / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
  if (num >= 1_000) return (num / 1_000).toFixed(1).replace(/\.0$/, "") + "k";
  return num.toLocaleString();
}

export function GithubStars() {
  const [stars, setStars] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`https://api.github.com/repos/${REPO}`, {
      headers: { Accept: "application/vnd.github.v3+json", "User-Agent": "wiwi" },
    })
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status}`);
        return res.json();
      })
      .then((data) => {
        if (!cancelled) setStars(data.stargazers_count ?? null);
      })
      .catch(() => {
        if (!cancelled) setStars(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <a
      href="https://github.com/shinmentakezo07/wiwia"
      target="_blank"
      rel="noopener noreferrer"
      className="group relative flex items-center gap-0.5 rounded-full p-1.5 text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]"
      aria-label={`GitHub - ${formatNumber(stars)} stars`}
    >
      <div className="relative">
        <Github className="size-5" />
        <Star
          className="absolute -right-1 -top-1 size-2.5 fill-yellow-400 stroke-yellow-400 transition-transform group-hover:scale-110"
          strokeWidth={2}
        />
      </div>
      <span className="ml-1 text-xs font-medium tabular-nums">{formatNumber(stars)}</span>
    </a>
  );
}
