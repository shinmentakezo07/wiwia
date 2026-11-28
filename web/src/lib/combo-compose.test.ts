import { describe, expect, it } from "vitest";
import {
  matchAcrossProviders,
  normalizeModelId,
  pickSplit,
  sanitizeComboName,
  suggestComboName,
  uniqueName,
} from "./combo-compose";

describe("normalizeModelId", () => {
  it("drops vendor prefixes and case", () => {
    expect(normalizeModelId("openai/GPT-5.2")).toBe("gpt-5.2");
    expect(normalizeModelId("z-ai/glm-4.6")).toBe("glm-4.6");
    expect(normalizeModelId("  Kimi-K2 ")).toBe("kimi-k2");
  });
});

describe("matchAcrossProviders", () => {
  const cat = {
    a: ["openai/gpt-5.2", "gpt-5.2-mini"],
    b: ["GPT-5.2"],
    c: ["gpt-5.2-codex", "gpt-5.2-pro-max"],
    d: ["glm-4.6"],
  };
  it("prefers exact normalized matches, falls back to shortest fuzzy", () => {
    expect(matchAcrossProviders("gpt-5.2", cat, ["a", "b", "c", "d"])).toEqual([
      { provider: "a", model_id: "openai/gpt-5.2", exact: true },
      { provider: "b", model_id: "GPT-5.2", exact: true },
      { provider: "c", model_id: "gpt-5.2-codex", exact: false },
    ]);
  });
  it("returns nothing for an empty query", () => {
    expect(matchAcrossProviders("  ", cat, ["a"])).toEqual([]);
  });
});

describe("names", () => {
  it("sanitizes", () => {
    expect(sanitizeComboName(" My Combo!! ")).toBe("my-combo");
  });
  it("uniqueName suffixes", () => {
    expect(uniqueName("x", ["x", "x-2"])).toBe("x-3");
    expect(uniqueName("y", ["x"])).toBe("y");
  });
  it("suggests the common model id, avoiding taken names", () => {
    expect(suggestComboName(["openai/gpt-5.2", "GPT-5.2", "glm-4.6"], [])).toBe("gpt-5.2");
    expect(suggestComboName(["gpt-5.2"], ["gpt-5.2"])).toBe("gpt-5.2-2");
    expect(suggestComboName([], [])).toBeNull();
  });
});

describe("pickSplit", () => {
  it("sums weights per provider", () => {
    expect(
      pickSplit([
        { provider: "a", weight: 3 },
        { provider: "b", weight: 1 },
        { provider: "a", weight: 0 },
      ]),
    ).toEqual([
      { provider: "a", weight: 4, share: 0.8 },
      { provider: "b", weight: 1, share: 0.2 },
    ]);
  });
});
