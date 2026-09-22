// BlogDetail — full article view for /blog/:slug. Renders one entry from the
// blog data module using the shared detail-page chrome.

import { Link, useParams } from "react-router-dom";
import { ArrowRight, Clock } from "lucide-react";
import {
  DetailCta,
  DetailHeader,
  DetailNotFound,
  FactTable,
  ProseSection,
} from "@/components/detail-page";
import { postBySlug, BLOG_POSTS } from "@/data/blog";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

export function BlogDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const post = slug ? postBySlug(slug) : undefined;

  if (!post) {
    return <DetailNotFound backTo="/blog" backLabel="All posts" what="Post" />;
  }

  const related = post.related
    .map((r) => BLOG_POSTS.find((p) => p.slug === r))
    .filter((p): p is (typeof BLOG_POSTS)[number] => Boolean(p));

  return (
    <div className="mx-auto max-w-3xl space-y-10 pb-16">
      <DetailHeader
        backTo="/blog"
        backLabel="All posts"
        badge={post.category}
        meta={`${formatDate(post.date)} · ${post.readMinutes} min read`}
        title={post.title}
        intro={post.intro}
      />

      <FactTable
        title="At a glance"
        rows={post.facts.map((f) => ({ label: f.label, value: f.value }))}
      />

      {post.sections.map((block, i) => (
        <ProseSection key={i} block={block} />
      ))}

      {related.length > 0 && (
        <section className="space-y-4">
          <span className="admin-label block">Read next</span>
          <div className="grid gap-3 sm:grid-cols-2">
            {related.map((r) => (
              <Link
                key={r.slug}
                to={`/blog/${r.slug}`}
                className="group rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-4 transition-colors hover:border-[var(--admin-border-hover)]"
              >
                <div className="flex items-center gap-2 text-[11px] text-[var(--admin-text-dim)]">
                  <span className="admin-badge admin-badge-gray">{r.category}</span>
                  <span className="inline-flex items-center gap-1" style={{ fontFamily: MONO }}>
                    <Clock size={11} /> {r.readMinutes} min
                  </span>
                </div>
                <h3 className="mt-2 text-[14px] font-semibold leading-snug text-[var(--admin-text)] transition-colors group-hover:text-blue-400">
                  {r.title}
                </h3>
                <span className="mt-2 inline-flex items-center gap-1 text-[12px] font-medium text-blue-400">
                  Read <ArrowRight size={13} className="transition-transform group-hover:translate-x-0.5" />
                </span>
              </Link>
            ))}
          </div>
        </section>
      )}

      <DetailCta
        title="Put it into practice"
        body="wiwi is one Docker image away. Point a client at it and the whole routing layer is live."
        primary={{ label: "Read the docs", to: "/docs" }}
        secondary={{ label: "Try the playground", to: "/playground" }}
      />
    </div>
  );
}
