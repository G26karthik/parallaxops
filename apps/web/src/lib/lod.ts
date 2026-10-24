/**
 * Streaming decoder for the worker's multi-resolution point clouds (format PXLD, see
 * services/vision-worker/.../worker/lod.py). The file is ordered coarse to fine, so points can be
 * drawn while the download is still running, and a client can stop reading at a level boundary.
 */

export interface LodHeader {
  total: number;
  /** Cumulative point count at the end of each level, strictly increasing, last = total. */
  levelEnds: number[];
  min: [number, number, number];
  max: [number, number, number];
}

const RECORD = 16;
const FIXED_HEAD = 40;
const MAX_LEVELS = 64;
/** Refuse absurd headers instead of allocating gigabytes for a corrupt or hostile file. */
export const MAX_TOTAL_POINTS = 50_000_000;

export class LodDecoder {
  header: LodHeader | null = null;
  loaded = 0;
  /** Stop decoding at this many points (a level boundary chosen by the caller). */
  limit = Number.POSITIVE_INFINITY;
  private pending: Uint8Array = new Uint8Array(0);
  private readonly onHeader: (h: LodHeader) => void;
  private readonly onPoints: (first: number, xyz: Float32Array, rgb: Uint8Array) => void;

  constructor(
    onHeader: (h: LodHeader) => void,
    onPoints: (first: number, xyz: Float32Array, rgb: Uint8Array) => void,
  ) {
    this.onHeader = onHeader;
    this.onPoints = onPoints;
  }

  get done(): boolean {
    return this.header !== null && this.loaded >= Math.min(this.header.total, this.limit);
  }

  push(chunk: Uint8Array): void {
    let buf = chunk;
    if (this.pending.length) {
      buf = new Uint8Array(this.pending.length + chunk.length);
      buf.set(this.pending);
      buf.set(chunk, this.pending.length);
    }
    if (!this.header) {
      const rest = this.readHeader(buf);
      if (!rest) {
        this.pending = buf;
        return;
      }
      buf = rest;
    }
    const total = (this.header as LodHeader).total;
    const n = Math.min(Math.floor(buf.length / RECORD), Math.min(total, this.limit) - this.loaded);
    if (n > 0) {
      const dv = new DataView(buf.buffer, buf.byteOffset, n * RECORD);
      const xyz = new Float32Array(n * 3);
      const rgb = new Uint8Array(n * 3);
      for (let i = 0; i < n; i++) {
        const o = i * RECORD;
        xyz[3 * i] = dv.getFloat32(o, true);
        xyz[3 * i + 1] = dv.getFloat32(o + 4, true);
        xyz[3 * i + 2] = dv.getFloat32(o + 8, true);
        rgb[3 * i] = dv.getUint8(o + 12);
        rgb[3 * i + 1] = dv.getUint8(o + 13);
        rgb[3 * i + 2] = dv.getUint8(o + 14);
      }
      const first = this.loaded;
      this.loaded += n;
      this.onPoints(first, xyz, rgb);
    }
    this.pending = buf.subarray(Math.max(n, 0) * RECORD);
  }

  /** Parse the header if all of it has arrived; returns the bytes after it, else null. */
  private readHeader(buf: Uint8Array): Uint8Array | null {
    if (buf.length < 16) return null;
    const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
    const magic = String.fromCharCode(buf[0] ?? 0, buf[1] ?? 0, buf[2] ?? 0, buf[3] ?? 0);
    if (magic !== "PXLD") throw new Error("Not a ParallaxOps point cloud");
    const version = dv.getUint32(4, true);
    if (version !== 1) throw new Error(`Unsupported point-cloud version ${version}`);
    const total = dv.getUint32(8, true);
    const levels = dv.getUint32(12, true);
    if (total < 1 || total > MAX_TOTAL_POINTS || levels < 1 || levels > MAX_LEVELS)
      throw new Error("Point-cloud header is out of range");
    const headBytes = Math.ceil((FIXED_HEAD + 4 * levels) / 16) * 16;
    if (buf.length < headBytes) return null;
    const f = (o: number) => dv.getFloat32(o, true);
    const levelEnds = Array.from({ length: levels }, (_, k) =>
      dv.getUint32(FIXED_HEAD + 4 * k, true),
    );
    levelEnds.forEach((e, k) => {
      if (e <= (levelEnds[k - 1] ?? 0)) throw new Error("Point-cloud levels are not increasing");
    });
    if (levelEnds[levels - 1] !== total) throw new Error("Point-cloud levels do not add up");
    this.header = {
      total,
      levelEnds,
      min: [f(16), f(20), f(24)],
      max: [f(28), f(32), f(36)],
    };
    this.onHeader(this.header);
    return buf.subarray(headBytes);
  }
}

/** The largest level boundary within the budget (never less than the first level). */
export function levelLimit(levelEnds: number[], budget: number): number {
  let best = levelEnds[0] ?? 0;
  for (const e of levelEnds) if (e <= budget) best = e;
  return best;
}

export interface StreamHandlers {
  onHeader: (h: LodHeader) => void;
  onPoints: (first: number, xyz: Float32Array, rgb: Uint8Array) => void;
}

/**
 * Download a point cloud and decode it while it arrives. Reading stops at the last level boundary
 * within `budget` points, so a huge cloud costs only the bytes the client will actually draw.
 */
export async function streamCloud(
  url: string,
  handlers: StreamHandlers,
  opts: { budget: number; signal?: AbortSignal },
): Promise<LodHeader> {
  const res = await fetch(url, { signal: opts.signal });
  if (!res.ok || !res.body) throw new Error(`points download failed (${res.status})`);
  const dec: LodDecoder = new LodDecoder((h) => {
    dec.limit = levelLimit(h.levelEnds, opts.budget);
    handlers.onHeader(h);
  }, handlers.onPoints);
  const reader = res.body.getReader();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      dec.push(value);
      if (dec.done) break;
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
  if (!dec.header || !dec.done) throw new Error("point cloud download ended early");
  return dec.header;
}
