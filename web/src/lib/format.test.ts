import { describe, expect, it } from "vitest";
import { fmtAgo, fmtInt, fmtPct, fmtTokens, fmtUsd, groupBy, mean, p95 } from "./format";

describe("fmtTokens", () => {
  it("keeps counts under a thousand exact", () => {
    expect(fmtTokens(0)).toBe("0");
    expect(fmtTokens(999)).toBe("999");
  });

  it("abbreviates thousands and millions", () => {
    expect(fmtTokens(1_500)).toBe("1.5k");
    expect(fmtTokens(2_500_000)).toBe("2.5M");
  });
});

describe("fmtUsd", () => {
  it("renders cents for ordinary amounts", () => {
    expect(fmtUsd(0)).toBe("$0.00");
    expect(fmtUsd(1.234)).toBe("$1.23");
  });

  it("keeps four decimals below a cent instead of rounding to zero", () => {
    expect(fmtUsd(0.0042)).toBe("$0.0042");
  });
});

describe("fmtPct", () => {
  it("renders one decimal place", () => {
    expect(fmtPct(0)).toBe("0.0%");
    expect(fmtPct(0.4567)).toBe("45.7%");
  });
});

describe("fmtAgo", () => {
  const now = 1_700_000_000_000; // ms

  it("handles a missing timestamp", () => {
    expect(fmtAgo(null, now)).toBe("never");
  });

  it("picks the largest sensible unit", () => {
    expect(fmtAgo(now / 1000 - 30, now)).toBe("30s ago");
    expect(fmtAgo(now / 1000 - 90, now)).toBe("1m ago");
    expect(fmtAgo(now / 1000 - 7200, now)).toBe("2h ago");
    expect(fmtAgo(now / 1000 - 90_000, now)).toBe("1d ago");
  });

  it("clamps future timestamps to zero instead of rendering a negative age", () => {
    expect(fmtAgo(now / 1000 + 60, now)).toBe("0s ago");
  });
});

describe("fmtInt", () => {
  it("drops fractional digits", () => {
    expect(fmtInt(12.9)).toBe("13");
  });
});

describe("groupBy", () => {
  it("buckets rows by key and labels an empty key", () => {
    const rows = [
      { id: 1, model: "a" },
      { id: 2, model: "a" },
      { id: 3, model: "" },
    ];
    const grouped = groupBy(rows, (r) => r.model);
    expect(grouped.get("a")?.map((r) => r.id)).toEqual([1, 2]);
    expect(grouped.get("(none)")?.map((r) => r.id)).toEqual([3]);
  });
});

describe("mean / p95", () => {
  it("returns zero for an empty series", () => {
    expect(mean([])).toBe(0);
    expect(p95([])).toBe(0);
  });

  it("averages and takes the nearest-rank 95th percentile", () => {
    expect(mean([1, 2, 3])).toBe(2);
    expect(p95([...Array(100).keys()])).toBe(95);
  });
});
