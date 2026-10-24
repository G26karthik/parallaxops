import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { LodDecoder, type LodHeader, levelLimit, streamCloud } from "../src/lib/lod.ts";

/** Written by the worker's encoder (services/vision-worker/tests/test_lod.py pins these bytes). */
const GOLDEN = new Uint8Array(
  readFileSync(new URL("../../../packages/contracts/fixtures/tiny.pxld", import.meta.url)),
);

interface Decoded {
  header: LodHeader | null;
  calls: number;
  xyz: number[];
  rgb: number[];
  firstSeen: number[];
}

function decode(bytes: Uint8Array, chunkSizes: number[], limit?: number): Decoded {
  const out: Decoded = { header: null, calls: 0, xyz: [], rgb: [], firstSeen: [] };
  const dec = new LodDecoder(
    (h) => {
      out.header = h;
    },
    (first, xyz, rgb) => {
      out.calls++;
      out.firstSeen.push(first);
      out.xyz.push(...xyz);
      out.rgb.push(...rgb);
    },
  );
  if (limit !== undefined) dec.limit = limit;
  let at = 0;
  for (let k = 0; at < bytes.length; k++) {
    const size = chunkSizes[k % chunkSizes.length] ?? bytes.length;
    dec.push(bytes.slice(at, at + size));
    at += size;
  }
  return out;
}

describe("PXLD decoding", () => {
  it("decodes the worker's file: every lattice point once, colours on their own point", () => {
    const d = decode(GOLDEN, [GOLDEN.length]);
    expect(d.header?.total).toBe(37);
    const ends = d.header?.levelEnds ?? [];
    expect(ends.length).toBeGreaterThanOrEqual(3);
    expect(ends.at(-1)).toBe(37);

    const seen = new Set<string>();
    let far = 0;
    for (let n = 0; n < 37; n++) {
      const [x, y, z] = [d.xyz[3 * n] ?? 0, d.xyz[3 * n + 1] ?? 0, d.xyz[3 * n + 2] ?? 0];
      if (x === 10) {
        far++;
        expect([y, z, d.rgb[3 * n], d.rgb[3 * n + 1], d.rgb[3 * n + 2]]).toEqual([
          10, 10, 255, 255, 255,
        ]);
        continue;
      }
      const [i, j] = [Math.round(x / 0.25), Math.round(y / 0.25)];
      expect(z).toBe((i * 0.5) % 1);
      // colour was i/5, j/5 on the way in; the PLY stores 8 bits, so allow one step of rounding
      expect(Math.abs((d.rgb[3 * n] ?? 0) - 51 * i)).toBeLessThanOrEqual(1);
      expect(Math.abs((d.rgb[3 * n + 1] ?? 0) - 51 * j)).toBeLessThanOrEqual(1);
      seen.add(`${i},${j}`);
    }
    expect(seen.size).toBe(36);
    expect(far).toBe(1);
    expect(d.header?.min).toEqual([0, 0, 0]);
    expect(d.header?.max).toEqual([10, 10, 10]);
  });

  it("gives the same result however the bytes are cut, and delivers points progressively", () => {
    const whole = decode(GOLDEN, [GOLDEN.length]);
    for (const sizes of [[1], [7], [16], [17, 3, 41], [100]]) {
      const cut = decode(GOLDEN, sizes);
      expect(cut.header).toEqual(whole.header);
      expect(cut.xyz).toEqual(whole.xyz);
      expect(cut.rgb).toEqual(whole.rgb);
    }
    const slow = decode(GOLDEN, [17]);
    expect(slow.calls).toBeGreaterThan(5); // points were handed over before the file ended
    expect(slow.firstSeen[0]).toBe(0);
    expect(slow.firstSeen).toEqual([...slow.firstSeen].sort((a, b) => a - b));
  });

  it("stops at the limit it was given, on a level boundary", () => {
    const first = decode(GOLDEN, [GOLDEN.length]).header?.levelEnds[0] ?? 0;
    const d = decode(GOLDEN, [13], first);
    expect(d.xyz.length).toBe(first * 3);
  });

  it("rejects foreign, future and inconsistent files", () => {
    const bad = (edit: (dv: DataView) => void) => {
      const copy = GOLDEN.slice();
      edit(new DataView(copy.buffer));
      return () => decode(copy, [copy.length]);
    };
    expect(() => decode(new Uint8Array(64), [64])).toThrow(/Not a ParallaxOps/);
    expect(bad((dv) => dv.setUint32(4, 2, true))).toThrow(/Unsupported/);
    expect(bad((dv) => dv.setUint32(8, 4_000_000_000, true))).toThrow(/out of range/);
    expect(bad((dv) => dv.setUint32(12, 0, true))).toThrow(/out of range/);
    expect(bad((dv) => dv.setUint32(8, 35, true))).toThrow(/do not add up/);
    expect(bad((dv) => dv.setUint32(40, 99, true))).toThrow(/not increasing/);
  });
});

describe("level selection", () => {
  it("takes the largest level within the budget, never less than the first", () => {
    const ends = [1000, 4000, 16000, 64000];
    expect(levelLimit(ends, 20000)).toBe(16000);
    expect(levelLimit(ends, 64000)).toBe(64000);
    expect(levelLimit(ends, 10)).toBe(1000);
  });
});

describe("streamCloud", () => {
  const respond = (bytes: Uint8Array, cut = bytes.length) =>
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(bytes.slice(0, cut), { status: 200 })),
    );

  it("reads only up to the budgeted level and reports the header", async () => {
    respond(GOLDEN);
    const got: number[] = [];
    const ends = decode(GOLDEN, [GOLDEN.length]).header?.levelEnds ?? [];
    const header = await streamCloud(
      "http://x/p",
      { onHeader: () => undefined, onPoints: (_f, xyz) => got.push(xyz.length / 3) },
      { budget: ends[0] ?? 0 },
    );
    expect(header.total).toBe(37);
    expect(got.reduce((a, b) => a + b, 0)).toBe(ends[0]);
  });

  it("fails loudly when the download ends early or the server refuses", async () => {
    respond(GOLDEN, GOLDEN.length - 20);
    const handlers = { onHeader: () => undefined, onPoints: () => undefined };
    await expect(streamCloud("http://x/p", handlers, { budget: 1e9 })).rejects.toThrow(
      /ended early/,
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("no", { status: 403 })),
    );
    await expect(streamCloud("http://x/p", handlers, { budget: 1e9 })).rejects.toThrow(/403/);
  });
});
