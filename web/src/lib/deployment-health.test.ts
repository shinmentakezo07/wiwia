import { describe, expect, it } from "vitest";
import type { DeploymentInfo } from "@/api/types";
import {
  comboReadiness,
  deploymentShare,
  deploymentState,
  deploymentWeight,
  duplicateDeploymentIdents,
  providerWeightSplit,
  routableDeployments,
} from "./deployment-health";

function dep(over: Partial<DeploymentInfo> = {}): DeploymentInfo {
  return {
    provider: "p1",
    model_id: "m1",
    weight: 1,
    available: true,
    inflight: 0,
    p95_latency_ms: 0,
    cooldown_remaining_s: 0,
    ...over,
  };
}

describe("routableDeployments", () => {
  it("returns every available deployment when none is on probation", () => {
    const deps = [dep({ model_id: "a" }), dep({ model_id: "b" })];
    expect(routableDeployments(deps)).toEqual(deps);
  });

  it("holds a probation deployment back while a healthy sibling is available", () => {
    const healthy = dep({ model_id: "a" });
    const probation = dep({ model_id: "b", probation: true });
    expect(routableDeployments([healthy, probation])).toEqual([healthy]);
  });

  it("falls back to probation deployments when every sibling is held back", () => {
    const probation = dep({ model_id: "b", probation: true });
    expect(routableDeployments([probation])).toEqual([probation]);
  });

  it("excludes unavailable deployments", () => {
    const cooling = dep({ model_id: "a", available: false, cooldown_remaining_s: 12 });
    const healthy = dep({ model_id: "b" });
    expect(routableDeployments([cooling, healthy])).toEqual([healthy]);
  });
});

describe("deploymentState", () => {
  it("separates routing from probation", () => {
    const healthy = dep({ model_id: "a" });
    const probation = dep({ model_id: "b", probation: true });
    const routable = routableDeployments([healthy, probation]);
    expect(deploymentState(healthy, routable)).toBe("routing");
    expect(deploymentState(probation, routable)).toBe("probation");
  });

  it("separates cooling from down", () => {
    const cooling = dep({ available: false, cooldown_remaining_s: 5 });
    const down = dep({ available: false, cooldown_remaining_s: 0 });
    expect(deploymentState(cooling, [])).toBe("cooling");
    expect(deploymentState(down, [])).toBe("down");
  });
});

describe("deploymentWeight", () => {
  it("clamps a zero or negative weight to one, matching the router", () => {
    expect(deploymentWeight(dep({ weight: 0 }))).toBe(1);
    expect(deploymentWeight(dep({ weight: -3 }))).toBe(1);
    expect(deploymentWeight(dep({ weight: 7 }))).toBe(7);
  });
});

describe("deploymentShare", () => {
  it("divides the routable weight by the routable total", () => {
    const deps = [
      dep({ model_id: "a", weight: 1 }),
      dep({ model_id: "b", weight: 3 }),
    ];
    expect(deploymentShare(deps[0], deps)).toBeCloseTo(0.25);
    expect(deploymentShare(deps[1], deps)).toBeCloseTo(0.75);
  });

  it("gives a held-back deployment zero share", () => {
    const healthy = dep({ model_id: "a", weight: 1 });
    const probation = dep({ model_id: "b", weight: 9, probation: true });
    expect(deploymentShare(probation, [healthy, probation])).toBe(0);
  });

  it("returns zero for an empty group", () => {
    expect(deploymentShare(dep(), [])).toBe(0);
  });
});

describe("comboReadiness", () => {
  it("counts each state once", () => {
    const deps = [
      dep({ model_id: "a" }),
      dep({ model_id: "b", probation: true }),
      dep({ model_id: "c", available: false, cooldown_remaining_s: 9 }),
      dep({ model_id: "d", available: false }),
    ];
    expect(comboReadiness(deps)).toEqual({
      total: 4,
      routable: 1,
      probation: 1,
      cooling: 1,
      down: 1,
    });
  });

  it("reports an empty group as empty, not healthy", () => {
    expect(comboReadiness([])).toEqual({
      total: 0,
      routable: 0,
      probation: 0,
      cooling: 0,
      down: 0,
    });
  });
});

describe("providerWeightSplit", () => {
  it("aggregates routable weight per provider, heaviest first", () => {
    const deps = [
      dep({ provider: "a", model_id: "m1", weight: 1 }),
      dep({ provider: "a", model_id: "m2", weight: 2 }),
      dep({ provider: "b", model_id: "m3", weight: 5 }),
    ];
    expect(providerWeightSplit(deps)).toEqual([
      { provider: "b", weight: 5 },
      { provider: "a", weight: 3 },
    ]);
  });

  it("ignores non-routable deployments", () => {
    const deps = [
      dep({ provider: "a" }),
      dep({ provider: "b", available: false, cooldown_remaining_s: 30 }),
    ];
    expect(providerWeightSplit(deps)).toEqual([{ provider: "a", weight: 1 }]);
  });
});

describe("duplicateDeploymentIdents", () => {
  it("reports an identity that appears twice", () => {
    const deps = [dep(), dep(), dep({ model_id: "other" })];
    expect(duplicateDeploymentIdents(deps)).toEqual(["p1/m1"]);
  });

  it("returns nothing when every route is unique", () => {
    expect(duplicateDeploymentIdents([dep(), dep({ model_id: "other" })])).toEqual([]);
  });
});
