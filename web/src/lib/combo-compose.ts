// Pure helpers behind the Combos composer: model-id normalization (so the same
// model registered under different vendor prefixes or casing is recognised as
// one), cross-provider matching, combo-name suggestion and the weighted split a
// set of picks will produce.

/** Lower-case a model id and drop any vendor prefix ("openai/", "z-ai/"). */
export function normalizeModelId(id: string): string {
  const s = id.trim().toLowerCase();
  const slash = s.lastIndexOf("/");
  return slash >= 0 ? s.slice(slash + 1) : s;
}

export interface ProviderMatch {
  provider: string;
  model_id: string;
  /** true when the normalized id equals the query exactly. */
  exact: boolean;
}

/**
 * For each provider, the best model id matching `query`: an exact normalized
 * match wins, otherwise the shortest id whose normalized form contains it.
 * One entry per provider, in the providers' input order.
 */
export function matchAcrossProviders(
  query: string,
  catalog: Record<string, string[]>,
  providers: string[],
): ProviderMatch[] {
  const q = normalizeModelId(query);
  if (!q) return [];
  const out: ProviderMatch[] = [];
  for (const p of providers) {
    const ids = catalog[p] ?? [];
    const exact = ids.find((m) => normalizeModelId(m) === q);
    if (exact) {
      out.push({ provider: p, model_id: exact, exact: true });
      continue;
    }
    const fuzzy = ids
      .filter((m) => normalizeModelId(m).includes(q))
      .sort((a, b) => a.length - b.length || a.localeCompare(b))[0];
    if (fuzzy) out.push({ provider: p, model_id: fuzzy, exact: false });
  }
  return out;
}

/** Turn arbitrary text into a legal combo name: no whitespace, safe chars only. */
export function sanitizeComboName(s: string): string {
  return s
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "-")
    .replace(/[^a-z0-9._:-]/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^[-.]+|[-.]+$/g, "");
}

/** `base`, or `base-2`, `base-3`… — the first one not in `taken`. */
export function uniqueName(base: string, taken: Iterable<string>): string {
  const set = new Set(taken);
  if (!set.has(base)) return base;
  for (let i = 2; ; i++) {
    const c = `${base}-${i}`;
    if (!set.has(c)) return c;
  }
}

/**
 * Suggest a combo name from picked model ids: the most common normalized id
 * (ties → shortest), sanitized and made unique. Null when nothing usable.
 */
export function suggestComboName(modelIds: string[], taken: Iterable<string>): string | null {
  const counts = new Map<string, number>();
  for (const m of modelIds) {
    const n = sanitizeComboName(normalizeModelId(m));
    if (n) counts.set(n, (counts.get(n) ?? 0) + 1);
  }
  const best = [...counts.entries()].sort(
    (a, b) => b[1] - a[1] || a[0].length - b[0].length || a[0].localeCompare(b[0]),
  )[0];
  return best ? uniqueName(best[0], taken) : null;
}

export interface SplitShare {
  provider: string;
  weight: number;
  share: number;
}

/** Per-provider share of the summed weight (each weight floored at 1). */
export function pickSplit(picks: { provider: string; weight: number }[]): SplitShare[] {
  const by = new Map<string, number>();
  for (const p of picks) by.set(p.provider, (by.get(p.provider) ?? 0) + Math.max(1, p.weight));
  const total = [...by.values()].reduce((a, b) => a + b, 0);
  return [...by.entries()]
    .map(([provider, weight]) => ({ provider, weight, share: total ? weight / total : 0 }))
    .sort((a, b) => b.weight - a.weight || a.provider.localeCompare(b.provider));
}
