import { describe, expect, it } from "vitest";
import { deltaVsPrevHour, hourlySeries } from "./dashboard-metrics";

describe("hourlySeries", () => {
  const nowMs = 1_700_000_000_000; // arbitrary fixed instant, ms
  const nowMin = Math.floor(nowMs / 60_000);

  it("returns twelve empty buckets when there are no points", () => {
    expect(hourlySeries([], nowMs)).toEqual(new Array(12).fill(0));
  });

  it("puts a point from the current minute in the last bucket", () => {
    const series = hourlySeries([{ t: nowMin * 60, v: 7 }], nowMs);
    expect(series[11]).toBe(7);
  });

  it("maps a point five minutes old to the previous bucket", () => {
    const series = hourlySeries([{ t: (nowMin - 5) * 60, v: 3 }], nowMs);
    expect(series[10]).toBe(3);
    expect(series[11]).toBe(0);
  });

  it("sums points that land in the same bucket", () => {
    const series = hourlySeries(
      [
        { t: nowMin * 60, v: 2 },
        { t: (nowMin - 1) * 60, v: 5 },
      ],
      nowMs,
    );
    expect(series[11]).toBe(7);
  });

  it("drops points older than the window and future points", () => {
    const series = hourlySeries(
      [
        { t: (nowMin - 60) * 60, v: 100 },
        { t: (nowMin + 5) * 60, v: 100 },
      ],
      nowMs,
    );
    expect(series.reduce((a, b) => a + b, 0)).toBe(0);
  });
});

describe("deltaVsPrevHour", () => {
  it("has nothing to compare against when the previous hour is zero", () => {
    expect(deltaVsPrevHour(10, 0)).toEqual({ pct: null, dir: "flat" });
  });

  it("reports a flat change for identical hours", () => {
    expect(deltaVsPrevHour(10, 10)).toEqual({ pct: 0, dir: "flat" });
  });

  it("reports direction and signed percent", () => {
    expect(deltaVsPrevHour(15, 10)).toEqual({ pct: 50, dir: "up" });
    expect(deltaVsPrevHour(5, 10)).toEqual({ pct: -50, dir: "down" });
  });
});
