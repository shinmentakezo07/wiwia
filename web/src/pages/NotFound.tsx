// NotFound — rendered inside PublicLayout for any unmatched path. The old
// catch-all silently redirected to the landing page, which hid broken links
// instead of surfacing them.

import { Link } from "react-router-dom";
import { ArrowLeft, BookOpen, SearchX } from "lucide-react";

export function NotFoundPage() {
  return (
    <div className="mx-auto flex max-w-2xl flex-col items-center px-6 py-24 text-center sm:py-32">
      <span className="flex h-14 w-14 items-center justify-center rounded-2xl border border-[var(--admin-border)] bg-white/[0.02]">
        <SearchX size={24} className="text-[var(--admin-text-dim)]" />
      </span>
      <p className="mt-6 font-mono text-[12px] uppercase tracking-[0.18em] text-[var(--admin-text-dim)]">
        404
      </p>
      <h1 className="mt-2 text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
        This page does not exist
      </h1>
      <p className="mt-4 max-w-md text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
        The link may be out of date, or the page may have moved. The docs and the
        model catalog are the best places to pick the thread back up.
      </p>
      <div className="mt-8 flex flex-col gap-3 sm:flex-row">
        <Link to="/" className="admin-btn admin-btn-primary">
          <ArrowLeft size={15} className="mr-2" />
          Back to the landing page
        </Link>
        <Link to="/docs" className="admin-btn admin-btn-ghost">
          <BookOpen size={15} className="mr-2" />
          Read the docs
        </Link>
      </div>
    </div>
  );
}
