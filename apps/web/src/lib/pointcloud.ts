/** Decoding and framing of the worker's point-cloud preview (see worker/artifacts.py). */

export interface Preview {
  count: number;
  xyz: Float32Array;
  rgb: Uint8Array;
}

export interface CameraPose {
  image: string;
  /** cam_from_world rotation (row-major 3x3) */
  R: number[][];
  center: [number, number, number];
}

export function parsePreview(buf: ArrayBuffer): Preview {
  const dv = new DataView(buf);
  const magic = String.fromCharCode(dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3));
  if (magic !== "PXPT") throw new Error("Not a ParallaxOps point-cloud preview");
  const version = dv.getUint32(4, true);
  if (version !== 1) throw new Error(`Unsupported preview version ${version}`);
  const count = dv.getUint32(8, true);
  const xyz = new Float32Array(buf, 16, count * 3);
  const rgb = new Uint8Array(buf, 16 + count * 12, count * 3);
  return { count, xyz, rgb };
}

export function parseCameras(json: {
  images: { image: string; cam_from_world: { rotation_matrix: number[][] }; center: number[] }[];
}): CameraPose[] {
  return json.images.map((i) => ({
    image: i.image,
    R: i.cam_from_world.rotation_matrix,
    center: i.center as [number, number, number],
  }));
}

export interface Framing {
  center: [number, number, number];
  radius: number;
  /** Per-point squared-distance trim: points farther than this (in model units) are "far". */
  trimDistance: number;
  farCount: number;
}

const median = (a: number[]) => {
  const s = [...a].sort((x, y) => x - y);
  return s[Math.floor(s.length / 2)] ?? 0;
};

/**
 * Frame the scene on the camera rig. SfM clouds contain a few very distant background points that
 * would shrink everything else to a speck; they are hidden by default (and the count is shown).
 */
export function frame(preview: Preview, cams: CameraPose[]): Framing {
  const cs = cams.length ? cams.map((c) => c.center) : [[0, 0, 0] as [number, number, number]];
  const center: [number, number, number] = [
    median(cs.map((c) => c[0])),
    median(cs.map((c) => c[1])),
    median(cs.map((c) => c[2])),
  ];
  const spread = Math.max(
    ...cs.map((c) => Math.hypot(c[0] - center[0], c[1] - center[1], c[2] - center[2])),
    1e-6,
  );
  const d = new Float32Array(preview.count);
  for (let i = 0; i < preview.count; i++) {
    d[i] = Math.hypot(
      (preview.xyz[3 * i] ?? 0) - center[0],
      (preview.xyz[3 * i + 1] ?? 0) - center[1],
      (preview.xyz[3 * i + 2] ?? 0) - center[2],
    );
  }
  const sorted = Float32Array.from(d).sort();
  const p85 = sorted[Math.floor(sorted.length * 0.85)] ?? spread;
  const radius = Math.max(p85, spread * 1.5);
  const trimDistance = radius * 2.5;
  let farCount = 0;
  for (const v of d) if (v > trimDistance) farCount++;
  return { center, radius, trimDistance, farCount };
}

/** COLMAP world (x right, y down, z forward) to a y-up display frame, normalised to unit radius. */
export function toDisplay(p: [number, number, number], f: Framing): [number, number, number] {
  return [
    (p[0] - f.center[0]) / f.radius,
    -(p[1] - f.center[1]) / f.radius,
    -(p[2] - f.center[2]) / f.radius,
  ];
}

/** A perceptually ordered ramp (viridis-like) for depth colouring. t in [0, 1]. */
export function ramp(t: number): [number, number, number] {
  const stops: [number, number, number][] = [
    [68, 1, 84],
    [59, 82, 139],
    [33, 145, 140],
    [94, 201, 98],
    [253, 231, 37],
  ];
  const x = Math.min(Math.max(t, 0), 1) * (stops.length - 1);
  const i = Math.min(Math.floor(x), stops.length - 2);
  const f = x - i;
  const a = stops[i] as [number, number, number];
  const b = stops[i + 1] as [number, number, number];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f].map(
    (v) => v / 255,
  ) as [number, number, number];
}
