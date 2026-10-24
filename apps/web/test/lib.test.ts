import { describe, expect, it } from "vitest";
import { ago, bytes, displayName, seconds, sha256Hex } from "../src/lib/format.ts";
import { frame, parseCameras, ramp, toDisplay } from "../src/lib/pointcloud.ts";
import { contentTypeOf } from "../src/lib/upload.ts";

function cloud(points: [number, number, number][]) {
  return {
    count: points.length,
    xyz: Float32Array.from(points.flat()),
    rgb: new Uint8Array(points.length * 3),
  };
}

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
    const f = frame(cloud(pts), cams);
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
