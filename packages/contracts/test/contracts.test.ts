import { describe, expect, it } from "vitest";
import {
  ALLOWED_CONTENT_TYPES,
  canTransition,
  JOB_STATES,
  keys,
  predecessors,
  ReconstructionConfigSchema,
  sanitizeFilename,
  TERMINAL_STATES,
  TRANSITIONS,
} from "../src/index.ts";

describe("job state machine", () => {
  it("terminal states have no outgoing edges", () => {
    for (const s of TERMINAL_STATES) expect(TRANSITIONS[s]).toEqual([]);
  });

  it("every non-terminal state can reach a terminal state", () => {
    for (const s of JOB_STATES) {
      if (TERMINAL_STATES.includes(s)) continue;
      const seen = new Set([s]);
      const q = [s];
      while (q.length) {
        for (const n of TRANSITIONS[q.pop() as (typeof JOB_STATES)[number]]) {
          if (seen.has(n)) continue;
          seen.add(n);
          q.push(n);
        }
      }
      expect([...seen].some((x) => TERMINAL_STATES.includes(x))).toBe(true);
    }
  });

  it("forbids skipping and moving backwards out of terminal states", () => {
    expect(canTransition("QUEUED", "COMPLETED")).toBe(false);
    expect(canTransition("COMPLETED", "QUEUED")).toBe(false);
    expect(canTransition("FAILED", "RECONSTRUCTING")).toBe(false);
    expect(canTransition("RECONSTRUCTING", "EVALUATING")).toBe(true);
    expect(canTransition("RECONSTRUCTING", "RECONSTRUCTING")).toBe(true); // idempotent replay
  });

  it("predecessors() is the inverse of TRANSITIONS", () => {
    expect(predecessors("COMPLETED")).toEqual(["EVALUATING"]);
    expect(predecessors("CANCELLED")).toContain("QUEUED");
    expect(predecessors("CANCELLED")).not.toContain("COMPLETED");
  });
});

describe("sanitizeFilename", () => {
  it.each([
    ["../../etc/passwd", "passwd"],
    ["C:\\Users\\x\\evil.jpg", "evil.jpg"],
    ["a\u0000b\u202e.jpg", "a_b_.jpg"],
    ["...hidden", "_hidden"],
    ["", "image"],
    ["   ", "image"],
  ])("%j -> %j", (raw, expected) => {
    expect(sanitizeFilename(raw)).toBe(expected);
  });

  it("caps the length", () => {
    expect(sanitizeFilename(`${"a".repeat(500)}.jpg`).length).toBe(120);
  });
});

describe("config bounds", () => {
  it("applies defaults", () => {
    const c = ReconstructionConfigSchema.parse({});
    expect(c.maxImageSize).toBe(1600);
    expect(c.mapper).toBe("incremental");
  });
  it("rejects values that would exhaust the laptop", () => {
    expect(ReconstructionConfigSchema.safeParse({ maxImageSize: 100000 }).success).toBe(false);
    expect(ReconstructionConfigSchema.safeParse({ maxNumFeatures: 10_000_000 }).success).toBe(
      false,
    );
  });
});

describe("object keys", () => {
  it("are tenant-prefixed and never contain user filenames", () => {
    const k = keys.image("tenant-abcdef12", "d", "i", "jpg");
    expect(k.startsWith("tenants/tenant-abcdef12/")).toBe(true);
    expect(ALLOWED_CONTENT_TYPES).toContain("image/jpeg");
  });
});
