import { describe, expect, it } from "vitest";
import { ago, bytes, displayName, seconds, sha256Hex } from "../src/lib/format.ts";
import { frame, parseCameras, parsePreview, ramp, toDisplay } from "../src/lib/pointcloud.ts";
import { contentTypeOf } from "../src/lib/upload.ts";

/** Build a buffer in the worker's preview format (see worker/artifacts.py). */
function preview(points: [number, number, number][], rgb = [10, 20, 30]) {
  const n = points.length;
  const buf = new ArrayBuffer(16 + n * 12 + n * 3);
  const dv = new DataView(buf);
  "PXPT".split("").forEach((c, i) => dv.setUint8(i, c.charCodeAt(0)));
  dv.setUint32(4, 1, true);
  dv.setUint32(8, n, true);
  points.forEach((p, i) => p.forEach((v, k) => dv.setFloat32(16 + i * 12 + k * 4, v, true)));
  for (let i = 0; i < n; i++) rgb.forEach((v, k) => dv.setUint8(16 + n * 12 + i * 3 + k, v));
  return buf;
}

describe("point-cloud preview decoding", () => {
  it("round-trips positions and colours", () => {
    const p = parsePreview(
      preview([
        [1, 2, 3],
        [-4, 5, 6.5],
      ]),
    );
    expect(p.count).toBe(2);
    expect([...p.xyz]).toEqual([1, 2, 3, -4, 5, 6.5]);
    expect([...p.rgb]).toEqual([10, 20, 30, 10, 20, 30]);
  });

  it("rejects foreign data and unknown versions", () => {
    expect(() => parsePreview(new ArrayBuffer(32))).toThrow(/Not a ParallaxOps/);
    const buf = preview([[0, 0, 0]]);
    new DataView(buf).setUint32(4, 2, true);
    expect(() => parsePreview(buf)).toThrow(/Unsupported preview version/);
  });
});

describe("scene framing", () => {
  const cams = parseCameras({
    images: [0, 1, 2].map((i) => ({
      image: `${i}.jpg`,
      cam_from_world: {
        rotation_matrix: [
          [1, 0, 0],
          [0, 1, 0],
          [0, 0, 1],
        ],
      },
      center: [i, 0, 0],
    })),
  });

  it("centres on the camera rig, not on far outliers", () => {
    const pts: [number, number, number][] = Array.from({ length: 200 }, (_, i) => [
      (i % 10) * 0.2,
      Math.floor(i / 10) * 0.1,
      2,
    ]);
    pts.push([500, 500, 500]); // distant background point
    const f = frame(parsePreview(preview(pts)), cams);
    expect(f.center[0]).toBeCloseTo(1, 5); // median of camera x = 1
    expect(f.farCount).toBe(1);
    expect(f.radius).toBeLessThan(50);
  });

  it("maps COLMAP (y down, z forward) to a y-up display frame", () => {
    const f = {
      center: [0, 0, 0] as [number, number, number],
      radius: 2,
      trimDistance: 5,
      farCount: 0,
    };
    expect(toDisplay([2, 2, 2], f)).toEqual([1, -1, -1]);
  });

  it("colour ramp stays in range and is monotone in brightness at the ends", () => {
    for (const t of [-1, 0, 0.5, 1, 2])
      for (const c of ramp(t)) expect(c).toBeGreaterThanOrEqual(0);
    expect(ramp(0)[2]).toBeGreaterThan(ramp(0)[0]); // starts dark purple/blue
    expect(ramp(1)[0]).toBeGreaterThan(0.9); // ends bright yellow
  });
});

describe("formatting and input helpers", () => {
  it("formats bytes, durations and relative times", () => {
    expect(bytes(512)).toBe("512 B");
    expect(bytes(1536)).toBe("1.5 KB");
    expect(seconds(0.108)).toBe("108 ms");
    expect(seconds(90)).toBe("1 min 30 s");
    expect(seconds(null)).toBe("-");
    const now = Date.parse("2026-01-01T00:10:00Z");
    expect(ago("2026-01-01T00:09:00Z", now)).toBe("1 min ago");
  });

  it("strips the worker's capture-order prefix for display only", () => {
    expect(displayName("0007_DSC_0284.JPG")).toBe("DSC_0284.JPG");
    expect(displayName("2020_holiday.jpg")).toBe("holiday.jpg");
    expect(displayName("IMG_0001.jpg")).toBe("IMG_0001.jpg");
  });

  it("hashes the same bytes the server will verify", async () => {
    const h = await sha256Hex(new Blob([new TextEncoder().encode("abc")]));
    expect(h).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });

  it("accepts only JPEG and PNG", () => {
    expect(contentTypeOf(new File([], "a.JPG"))).toBe("image/jpeg");
    expect(contentTypeOf(new File([], "a.png", { type: "image/png" }))).toBe("image/png");
    expect(contentTypeOf(new File([], "a.svg", { type: "image/svg+xml" }))).toBeNull();
    expect(contentTypeOf(new File([], "payload.exe"))).toBeNull();
  });
});
