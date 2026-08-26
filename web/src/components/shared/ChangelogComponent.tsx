// Changelog — displays a list of changelog entries with date, title, summary,
// and an image. Ported from the Next.js reference's changelog.tsx. Uses
// react-router Link and <img> instead of next/image.

import { Link } from "react-router-dom";

export interface ChangelogEntry {
  id: string;
  title: string;
  date: string;
  summary: string;
  slug: string;
  image: { src: string; alt: string; width: number; height: number };
}

interface ChangelogProps {
  entries?: ChangelogEntry[];
}

export function Changelog({ entries }: ChangelogProps = {}) {
  const changelogEntries = entries ?? [];

  return (
    <div className="min-h-screen bg-[var(--admin-bg)] pt-30 font-sans text-[var(--admin-text)]">
      <main className="container mx-auto px-4 py-16 sm:px-6 md:py-24 lg:px-8">
        <div className="mb-12 md:mb-16">
          <div className="mb-4 flex flex-col items-start justify-between md:flex-row md:items-center">
            <h1 className="text-4xl font-bold tracking-tight md:text-5xl">Changelog</h1>
          </div>
          <p className="text-[var(--admin-text-muted)]">
            Stay up to date with the latest features, improvements, and fixes in wiwi.
          </p>
        </div>

        <div className="mx-auto max-w-5xl space-y-16">
          {changelogEntries.map((entry) => (
            <article key={entry.id} className="grid gap-x-8 gap-y-4 md:grid-cols-[150px_1fr]">
              <div className="z-20 self-start bg-[var(--admin-bg)]/80 backdrop-blur md:sticky md:top-24">
                <time
                  dateTime={entry.date}
                  className="block pt-1 text-right text-sm text-[var(--admin-text-muted)]"
                >
                  {new Date(entry.date).toLocaleDateString("en-US", {
                    year: "numeric",
                    month: "long",
                    day: "numeric",
                  })}
                </time>
              </div>
              <div className="space-y-8">
                <div className="space-y-4">
                  <h2 className="text-2xl font-medium transition-colors hover:text-[var(--admin-text-muted)]">
                    <Link to={`/changelog/${entry.slug}`}>{entry.title}</Link>
                  </h2>
                  <p className="leading-relaxed text-[var(--admin-text-muted)]">{entry.summary}</p>
                  <Link
                    to={`/changelog/${entry.slug}`}
                    className="text-sm text-blue-400 hover:text-blue-300"
                  >
                    Read more
                    <span className="sr-only"> about {entry.title}</span> →
                  </Link>
                </div>
                <div className="overflow-hidden rounded-lg border border-[var(--admin-border)] bg-[var(--admin-surface)]">
                  <Link to={`/changelog/${entry.slug}`}>
                    <img
                      src={entry.image.src}
                      alt={entry.image.alt}
                      width={entry.image.width}
                      height={entry.image.height}
                      className="h-64 w-full rounded-lg object-cover object-top opacity-90 transition-opacity hover:opacity-100"
                    />
                  </Link>
                </div>
              </div>
            </article>
          ))}
        </div>
      </main>
    </div>
  );
}
