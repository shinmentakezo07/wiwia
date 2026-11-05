import { describe, expect, it, vi } from "vitest";
import { lazyWithReload, recoverChunkError } from "./chunk-recovery";

describe("stale deployment recovery", () => {
  it("reloads once when a deployment chunk has disappeared", () => {
    const reload = vi.fn();
    const storage = { getItem: () => null, setItem: vi.fn() };
    recoverChunkError(new TypeError("Failed to fetch dynamically imported module"), {
      reload,
      storage,
      now: () => 1_000_000,
      online: true,
    });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("does not reload again during the retry window", () => {
    const reload = vi.fn();
    recoverChunkError(new TypeError("Failed to fetch dynamically imported module"), {
      reload,
      storage: { getItem: () => "1000", setItem: vi.fn() },
      now: () => 2000,
    });
    expect(reload).not.toHaveBeenCalled();
  });

  it("allows a later deployment after the retry window", () => {
    const reload = vi.fn();
    recoverChunkError(new TypeError("error loading dynamically imported module"), {
      reload,
      storage: { getItem: () => "1000", setItem: () => undefined },
      now: () => 1_000_001,
      online: true,
    });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("does not reload ordinary page errors", () => {
    const reload = vi.fn();
    recoverChunkError(new Error("Cannot read properties of undefined"), {
      reload,
      storage: { getItem: () => null, setItem: () => undefined },
    });
    expect(reload).not.toHaveBeenCalled();
  });

  it("does not reload while offline", () => {
    const reload = vi.fn();
    recoverChunkError(new TypeError("Failed to fetch dynamically imported module"), {
      reload,
      online: false,
    });
    expect(reload).not.toHaveBeenCalled();
  });

  it("does not reload when session storage is unavailable", () => {
    const reload = vi.fn();
    recoverChunkError(new TypeError("Failed to fetch dynamically imported module"), {
      reload,
      storage: {
        getItem: () => { throw new Error("storage unavailable"); },
        setItem: () => { throw new Error("storage unavailable"); },
      },
    });
    expect(reload).not.toHaveBeenCalled();
  });
});

describe("lazyWithReload", () => {
  it("returns a successfully loaded module", async () => {
    const module = { default: "page" };
    await expect(lazyWithReload(() => Promise.resolve(module))()).resolves.toBe(module);
  });

  it("leaves ordinary import errors to the error boundary", async () => {
    const error = new Error("ordinary page failure");
    await expect(lazyWithReload(() => Promise.reject(error))()).rejects.toBe(error);
  });

  it("reloads a missing route module once and keeps the old tab pending", async () => {
    const reload = vi.fn();
    const storage = { getItem: () => null, setItem: vi.fn() };
    const pending = lazyWithReload(
      () => Promise.reject(new TypeError("Failed to fetch dynamically imported module")),
      { reload, storage, online: true },
    )();
    await Promise.resolve();
    expect(reload).toHaveBeenCalledTimes(1);
    await expect(Promise.race([
      pending.then(() => "settled"),
      new Promise<"pending">((resolve) => setTimeout(() => resolve("pending"), 0)),
    ])).resolves.toBe("pending");
  });
});
