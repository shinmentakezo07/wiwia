// Pure derivations for dashboard tiles: hourly sparkline series + prev-hour
// deltas. Kept free of React so they stay trivially testable.

export interface Delta {
  pct: number | null;
  dir: "up" | "down" | "flat";
}

/** Sum `points` into twelve 5-minute buckets covering the last hour
 *  (oldest first). Points carry epoch-seconds; `nowMs` is injectable for tests. */
export function hourlySeries(
  points: Array<{ t: number; v: number }>,
  nowMs = Date.now(),
): number[] {
  const nowMin = Math.floor(nowMs / 60_000);
  const sums = new Array<number>(12).fill(0);
  for (const p of points) {
    const m = Math.floor(p.t / 60);
    const age = nowMin - m; // 0 = current 5-min slot
    if (age < 0 || age >= 60) continue;
    sums[11 - Math.floor(age / 5)] += p.v;
  }
  return sums;
}

/** Signed percent change vs the previous hour. Null when there is no previous
 *  value to compare against (rendered as a muted "—" chip, never ∞%). */
export function deltaVsPrevHour(current: number, previous: number): Delta {
  if (previous <= 0) return { pct: null, dir: "flat" };
  if (current === previous) return { pct: 0, dir: "flat" };
  const pct = ((current - previous) / previous) * 100;
  return { pct, dir: pct > 0 ? "up" : "down" };
}
