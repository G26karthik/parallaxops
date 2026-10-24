import { afterEach, describe, expect, it, vi } from "vitest";
import { getTenantId } from "../src/lib/tenant.ts";

function stubStorage(storage: unknown) {
  vi.stubGlobal("window", { localStorage: storage });
}

afterEach(() => vi.unstubAllGlobals());

describe("tenant id", () => {
  it("is created once and reused when storage works", () => {
    const data = new Map<string, string>();
    stubStorage({
      getItem: (k: string) => data.get(k) ?? null,
      setItem: (k: string, v: string) => void data.set(k, v),
    });
    const first = getTenantId();
    expect(first).toMatch(/^t-[0-9a-f]{20}$/);
    expect(getTenantId()).toBe(first);
    expect(data.get("parallaxops.tenant")).toBe(first);
  });

  it("stays the same for every caller when storage is blocked", () => {
    // Every component that asks for the tenant must agree, or each would see its own empty data.
    stubStorage({
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    });
    const a = getTenantId();
    expect(a).toMatch(/^t-[0-9a-f]{20}$/);
    expect(getTenantId()).toBe(a);
    expect(getTenantId()).toBe(a);
  });
});
