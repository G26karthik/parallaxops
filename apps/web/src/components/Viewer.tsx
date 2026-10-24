"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { unwrap } from "../lib/api.ts";
import { num } from "../lib/format.ts";
import { useApi } from "../lib/hooks.ts";
import { type LodHeader, levelLimit, streamCloud } from "../lib/lod.ts";
import { SCENE_NOTE } from "../lib/metric-help.ts";
import {
  type CameraPose,
  type Framing,
  frame,
  parseCameras,
  ramp,
  toDisplay,
} from "../lib/pointcloud.ts";
import { Skeleton } from "./ui.tsx";

type ColorMode = "rgb" | "depth" | "flat";

type Source = "sparse" | "dense";

/** Most points the browser keeps; a larger cloud is read up to the last level within this. */
const POINT_BUDGET = 3_000_000;
/** While the camera moves, draw the coarsest level set that stays within this many points. */
const MOVING_POINTS = 150_000;
/** Quiet time after the last camera change before the full detail is drawn again. */
const SETTLE_MS = 180;

interface SceneMeta {
  cams: CameraPose[];
  cloudUrl: string;
  sparseCount: number;
  denseCount: number | null;
  hasDense: boolean;
}

function useScene(jobId: string, source: Source) {
  const { api, tenantId } = useApi();
  return useQuery({
    queryKey: ["scene", tenantId, jobId, source],
    enabled: !!api,
    staleTime: 10 * 60_000,
    placeholderData: keepPreviousData, // keep showing the current view while the other level loads
    queryFn: async (): Promise<SceneMeta> => {
      const s = await unwrap(api!.GET("/v1/jobs/{jobId}/scene", { params: { path: { jobId } } }));
      const camsRes = await fetch(s.camerasUrl);
      if (!camsRes.ok) throw new Error(`cameras download failed (${camsRes.status})`);
      return {
        cams: parseCameras(await camsRes.json()),
        cloudUrl: source === "dense" && s.densePointsUrl ? s.densePointsUrl : s.pointsUrl,
        sparseCount: s.pointCount,
        denseCount: s.densePointCount,
        hasDense: !!s.densePointsUrl,
      };
    },
  });
}

function buildFrustums(cams: CameraPose[], f: Framing): THREE.LineSegments {
  const size = 0.06;
  const corners: [number, number, number][] = [
    [-1, -0.66, 1.4],
    [1, -0.66, 1.4],
    [1, 0.66, 1.4],
    [-1, 0.66, 1.4],
  ];
  const pos: number[] = [];
  for (const c of cams) {
    const world = corners.map((q) => {
      const s = [q[0] * size * f.radius, q[1] * size * f.radius, q[2] * size * f.radius];
      // world = R^T * cam + center
      return [0, 1, 2].map(
        (k) =>
          (c.R[0]?.[k] ?? 0) * (s[0] ?? 0) +
          (c.R[1]?.[k] ?? 0) * (s[1] ?? 0) +
          (c.R[2]?.[k] ?? 0) * (s[2] ?? 0) +
          (c.center[k] ?? 0),
      ) as [number, number, number];
    });
    const apex = toDisplay(c.center, f);
    const d = world.map((w) => toDisplay(w, f));
    for (let i = 0; i < 4; i++) {
      const a = d[i] as number[];
      const b = d[(i + 1) % 4] as number[];
      pos.push(...a, ...b, ...apex, ...a);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  return new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: 0xf2b441 }));
}

interface Progress {
  loaded: number;
  total: number;
  /** Points the browser will load (a level boundary within POINT_BUDGET). */
  target: number;
  levelEnds: number[];
  drawn: number;
  done: boolean;
}

interface Settings {
  mode: ColorMode;
  trim: boolean;
  /** Index of the finest level to draw when the camera is still. */
  level: number;
}

function Scene({ meta, jobId }: { meta: SceneMeta; jobId: string }) {
  const host = useRef<HTMLDivElement>(null);
  const ctl = useRef<{
    frustums?: THREE.LineSegments;
    axes?: THREE.AxesHelper;
    material?: THREE.PointsMaterial;
    render?: () => void;
    redraw?: () => void;
    recolor?: () => void;
    reset?: () => void;
    shot?: () => void;
    goTo?: (i: number) => void;
  }>({});
  const settings = useRef<Settings>({ mode: "rgb", trim: true, level: Number.MAX_SAFE_INTEGER });
  const [mode, setMode] = useState<ColorMode>("rgb");
  const [size, setSize] = useState(2.5);
  const [showCams, setShowCams] = useState(true);
  const [showAxes, setShowAxes] = useState(false);
  const [trim, setTrim] = useState(true);
  const [level, setLevel] = useState(Number.MAX_SAFE_INTEGER);
  const [webgl, setWebgl] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [farCount, setFarCount] = useState(0);
  const { cams, cloudUrl } = meta;

  // Build the scene once per cloud. Points are appended as the stream delivers them; display
  // settings are applied by the effects below.
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    } catch {
      setWebgl(false);
      return;
    }
    const started = performance.now();
    setError(null);
    setProgress(null);
    setFarCount(0);
    const style = getComputedStyle(document.documentElement);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(new THREE.Color(style.getPropertyValue("--bg").trim() || "#0b0d10"));
    el.appendChild(renderer.domElement);
    renderer.domElement.tabIndex = 0;
    renderer.domElement.setAttribute("role", "img");
    renderer.domElement.setAttribute(
      "aria-label",
      `3D point cloud with ${cams.length} cameras. Arrow keys orbit, plus and minus zoom.`,
    );

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(50, 1, 0.01, 100);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false;
    controls.listenToKeyEvents(renderer.domElement);
    controls.keyPanSpeed = 12;

    const geo = new THREE.BufferGeometry();
    const material = new THREE.PointsMaterial({
      size: 2.5,
      sizeAttenuation: false,
      vertexColors: true,
    });
    const points = new THREE.Points(geo, material);
    points.frustumCulled = false; // positions arrive over time, a bounding sphere would go stale
    scene.add(points);
    const axes = new THREE.AxesHelper(0.5);
    axes.visible = false;
    scene.add(axes);
    Object.assign(ctl.current, { axes, material });

    // Filled in when the header arrives.
    let header: LodHeader | null = null;
    let target = 0;
    let loaded = 0;
    let raw = new Float32Array(0);
    let rawRgb = new Uint8Array(0);
    let depth = new Float32Array(0);
    let pos: THREE.BufferAttribute | null = null;
    let col: THREE.BufferAttribute | null = null;
    let framing: Framing | null = null;
    let moving = false;
    let settle: ReturnType<typeof setTimeout> | undefined;
    let lastUi = 0;
    let firstMs: number | null = null;
    let doneMs: number | null = null;
    let finished = false;

    const render = () => renderer.render(scene, camera);
    const drawCount = () => {
      if (!header) return 0;
      const ends = header.levelEnds;
      const wanted = Math.min(settings.current.level, ends.length - 1);
      const detail = Math.min(loaded, ends[wanted] ?? loaded);
      return moving ? Math.min(detail, levelLimit(ends, MOVING_POINTS)) : detail;
    };
    const publish = (force: boolean) => {
      const drawn = drawCount();
      el.dataset.loaded = String(loaded);
      el.dataset.total = String(header?.total ?? 0);
      el.dataset.target = String(target);
      el.dataset.drawn = String(drawn);
      el.dataset.levels = header?.levelEnds.join(",") ?? "";
      if (firstMs !== null) el.dataset.firstMs = String(Math.round(firstMs));
      if (doneMs !== null) el.dataset.doneMs = String(Math.round(doneMs));
      const now = performance.now();
      if (header && (force || now - lastUi > 150)) {
        lastUi = now;
        setProgress({
          loaded,
          total: header.total,
          target,
          levelEnds: header.levelEnds,
          drawn,
          done: finished,
        });
      }
    };
    const redraw = () => {
      geo.setDrawRange(0, drawCount());
      render();
      publish(false);
    };

    /** Display position, distance and colour for points [a, b). */
    const fill = (a: number, b: number, recolorOnly: boolean) => {
      if (!framing || !pos || !col) return;
      const { mode: m, trim: t } = settings.current;
      const pa = pos.array as Float32Array;
      const ca = col.array as Uint8Array;
      const maxD = framing.trimDistance / framing.radius;
      for (let i = a; i < b; i++) {
        if (!recolorOnly) {
          const p = toDisplay([raw[3 * i] ?? 0, raw[3 * i + 1] ?? 0, raw[3 * i + 2] ?? 0], framing);
          pa[3 * i] = p[0];
          pa[3 * i + 1] = p[1];
          pa[3 * i + 2] = p[2];
          depth[i] = Math.hypot(p[0], p[1], p[2]);
        }
        const far = t && (depth[i] ?? 0) > maxD;
        // Hidden far points are collapsed to the clear colour instead of rebuilding the geometry.
        let r = 0;
        let g = 0;
        let bl = 0;
        if (!far) {
          if (m === "rgb") {
            r = rawRgb[3 * i] ?? 0;
            g = rawRgb[3 * i + 1] ?? 0;
            bl = rawRgb[3 * i + 2] ?? 0;
          } else {
            const c = m === "depth" ? ramp(Math.min((depth[i] ?? 0) / 1.6, 1)) : [0.3, 0.76, 0.77];
            r = Math.round((c[0] ?? 0) * 255);
            g = Math.round((c[1] ?? 0) * 255);
            bl = Math.round((c[2] ?? 0) * 255);
          }
        }
        ca[3 * i] = r;
        ca[3 * i + 1] = g;
        ca[3 * i + 2] = bl;
      }
      if (!recolorOnly) {
        pos.addUpdateRange(3 * a, 3 * (b - a));
        pos.needsUpdate = true;
      }
      col.addUpdateRange(3 * a, 3 * (b - a));
      col.needsUpdate = true;
    };

    const home = () => {
      camera.position.set(0.9, 0.55, 1.6);
      controls.target.set(0, 0, 0);
      controls.update();
      render();
    };
    const resize = () => {
      const w = el.clientWidth;
      const h = el.clientHeight;
      renderer.setSize(w, h, false);
      camera.aspect = w / Math.max(h, 1);
      camera.updateProjectionMatrix();
      render();
    };
    // Coarse while the camera moves, full detail once it has been still for a moment.
    const onChange = () => {
      if (loaded > 0 && header && !moving) {
        moving = true;
        redraw();
      } else render();
      clearTimeout(settle);
      settle = setTimeout(() => {
        moving = false;
        redraw();
      }, SETTLE_MS);
    };
    controls.addEventListener("change", onChange);
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();

    ctl.current.render = render;
    ctl.current.redraw = redraw;
    ctl.current.recolor = () => {
      fill(0, loaded, true);
      render();
    };
    ctl.current.reset = home;
    ctl.current.shot = () =>
      renderer.domElement.toBlob((b) => {
        if (!b) return;
        const a = document.createElement("a");
        a.href = URL.createObjectURL(b);
        a.download = `parallaxops-${jobId.slice(0, 8)}.png`;
        a.click();
        URL.revokeObjectURL(a.href);
      });
    ctl.current.goTo = (i: number) => {
      const c = cams[i];
      if (!c || !framing) return;
      const p = toDisplay(c.center, framing);
      camera.position.set(...p);
      // Camera looks along +z in COLMAP; world forward = R^T * (0,0,1), then display flip.
      const fwd = [c.R[2]?.[0] ?? 0, c.R[2]?.[1] ?? 0, c.R[2]?.[2] ?? 1];
      controls.target.set(
        p[0] + (fwd[0] ?? 0) * 0.5,
        p[1] - (fwd[1] ?? 0) * 0.5,
        p[2] - (fwd[2] ?? 0) * 0.5,
      );
      controls.update();
      render();
    };

    const abort = new AbortController();
    streamCloud(
      cloudUrl,
      {
        onHeader: (h) => {
          header = h;
          target = levelLimit(h.levelEnds, POINT_BUDGET);
          raw = new Float32Array(target * 3);
          rawRgb = new Uint8Array(target * 3);
          depth = new Float32Array(target);
          pos = new THREE.BufferAttribute(new Float32Array(target * 3), 3);
          col = new THREE.BufferAttribute(new Uint8Array(target * 3), 3, true);
          geo.setAttribute("position", pos);
          geo.setAttribute("color", col);
          geo.setDrawRange(0, 0);
          renderer.domElement.setAttribute(
            "aria-label",
            `3D point cloud with ${h.total} points and ${cams.length} cameras. Arrow keys orbit, plus and minus zoom.`,
          );
        },
        onPoints: (first, xyz, rgb) => {
          raw.set(xyz, 3 * first);
          rawRgb.set(rgb, 3 * first);
          const before = loaded;
          loaded = first + xyz.length / 3;
          const firstLevel = header?.levelEnds[0] ?? 0;
          if (!framing) {
            // Framing needs a spatially even sample of the whole cloud: the first level is one.
            if (loaded < firstLevel) return;
            framing = frame({ count: firstLevel, xyz: raw, rgb: rawRgb }, cams);
            setFarCount(framing.farCount);
            const fr = buildFrustums(cams, framing);
            fr.visible = ctl.current.frustums?.visible ?? true;
            scene.add(fr);
            ctl.current.frustums = fr;
            fill(0, loaded, false);
            home();
            firstMs = performance.now() - started;
          } else fill(before, loaded, false);
          redraw();
        },
      },
      { budget: POINT_BUDGET, signal: abort.signal },
    )
      .then(() => {
        finished = true;
        doneMs = performance.now() - started;
        publish(true);
      })
      .catch((e: unknown) => {
        if (!abort.signal.aborted) setError(e instanceof Error ? e.message : String(e));
      });

    return () => {
      abort.abort();
      clearTimeout(settle);
      ro.disconnect();
      controls.dispose();
      geo.dispose();
      material.dispose();
      const fr = ctl.current.frustums;
      if (fr) {
        scene.remove(fr);
        fr.geometry.dispose();
        (fr.material as THREE.Material).dispose();
        ctl.current.frustums = undefined;
      }
      renderer.dispose();
      renderer.domElement.remove();
      for (const k of Object.keys(el.dataset)) delete el.dataset[k];
    };
  }, [cloudUrl, cams, jobId]);

  // Apply settings.
  useEffect(() => {
    settings.current.mode = mode;
    settings.current.trim = trim;
    ctl.current.recolor?.();
  }, [mode, trim]);
  useEffect(() => {
    settings.current.level = level;
    ctl.current.redraw?.();
  }, [level]);
  useEffect(() => {
    const c = ctl.current;
    if (c.material) c.material.size = size;
    c.render?.();
  }, [size]);
  useEffect(() => {
    const c = ctl.current;
    if (c.frustums) c.frustums.visible = showCams;
    if (c.axes) c.axes.visible = showAxes;
    c.render?.();
  }, [showCams, showAxes]);

  if (!webgl)
    return (
      <div role="alert" className="panel p-6 text-sm">
        This browser could not create a WebGL context, so the interactive view is unavailable. The
        point cloud is still downloadable below (PLY).
      </div>
    );

  const levels = progress?.levelEnds.filter((e) => e <= progress.target) ?? [];
  const chosen = Math.min(level, Math.max(levels.length - 1, 0));
  return (
    <div className="panel overflow-hidden">
      <div ref={host} className="h-[460px] w-full sm:h-[560px]" />
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-line px-3 py-2.5 text-xs">
        <label className="flex items-center gap-2">
          Colour
          <select
            className="input !w-auto !py-1"
            value={mode}
            onChange={(e) => setMode(e.target.value as ColorMode)}
          >
            <option value="rgb">Photo colour</option>
            <option value="depth">Distance</option>
            <option value="flat">Flat</option>
          </select>
        </label>
        <label className="flex items-center gap-2">
          Point size
          <input
            type="range"
            min={1}
            max={8}
            step={0.5}
            value={size}
            onChange={(e) => setSize(Number(e.target.value))}
            className="accent-[var(--accent)]"
          />
        </label>
        {levels.length > 1 ? (
          <label
            className="flex items-center gap-2"
            title="Each level adds points between the ones already shown. Lower levels draw faster."
          >
            Detail
            <select
              className="input !w-auto !py-1"
              value={chosen}
              onChange={(e) => setLevel(Number(e.target.value))}
            >
              {levels.map((e, i) => (
                <option key={e} value={i}>
                  {i === levels.length - 1 ? "Full" : `Level ${i + 1}`} ({num(e)} points)
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={showCams}
            onChange={(e) => setShowCams(e.target.checked)}
          />{" "}
          Cameras ({cams.length})
        </label>
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={showAxes}
            onChange={(e) => setShowAxes(e.target.checked)}
          />{" "}
          Axes
        </label>
        <label
          className="flex items-center gap-1.5"
          title="Hides very distant background points so the subject fills the view"
        >
          <input type="checkbox" checked={trim} onChange={(e) => setTrim(e.target.checked)} /> Hide
          far points
          {farCount ? <span className="num text-muted">({num(farCount)})</span> : null}
        </label>
        {cams.length ? (
          <label className="flex items-center gap-2">
            View from
            <select
              className="input !w-auto !py-1"
              defaultValue=""
              onChange={(e) => e.target.value !== "" && ctl.current.goTo?.(Number(e.target.value))}
            >
              <option value="">camera…</option>
              {cams.map((c, i) => (
                <option key={c.image} value={i}>
                  {c.image.replace(/^\d{4}_/, "")}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <span className="ml-auto flex gap-2">
          <button type="button" className="btn !px-2.5 !py-1" onClick={() => ctl.current.reset?.()}>
            Reset view
          </button>
          <button type="button" className="btn !px-2.5 !py-1" onClick={() => ctl.current.shot?.()}>
            Save PNG
          </button>
        </span>
      </div>
      {error ? (
        <p role="alert" className="border-t border-line px-3 py-2 text-xs text-bad">
          Could not load the point cloud: {error}
        </p>
      ) : null}
      <p className="border-t border-line px-3 py-2 text-xs text-muted">
        {progress ? (
          <>
            <span className="num">{num(progress.drawn)}</span> of{" "}
            <span className="num">{num(progress.total)}</span> points drawn
            {progress.target < progress.total
              ? `; the browser keeps ${num(progress.target)} of them`
              : ""}
            {progress.done
              ? ""
              : `, loading ${Math.round((100 * progress.loaded) / progress.target)}%`}
            . Drag to orbit, scroll to zoom, right-drag to pan; arrow keys orbit when focused.{" "}
          </>
        ) : (
          "Loading the point cloud. "
        )}
        {SCENE_NOTE}
      </p>
    </div>
  );
}

export function Viewer({ jobId }: { jobId: string }) {
  const [source, setSource] = useState<Source>("sparse");
  const q = useScene(jobId, source);
  if (q.isLoading) return <Skeleton className="h-[460px]" />;
  if (q.isError)
    return (
      <div role="alert" className="panel p-4 text-sm text-bad">
        Could not load the 3D scene: {(q.error as Error).message}
      </div>
    );
  if (!q.data) return null;
  const d = q.data;
  return (
    <div className="space-y-2">
      {d.hasDense ? (
        <fieldset className="flex items-center gap-4 text-sm" aria-label="Point cloud level">
          {(["sparse", "dense"] as const).map((s) => (
            <label key={s} className="flex items-center gap-1.5">
              <input
                type="radio"
                name="source"
                checked={source === s}
                onChange={() => setSource(s)}
              />
              {s === "sparse"
                ? `Sparse SfM (${num(d.sparseCount)} points)`
                : `Dense MVS (${num(d.denseCount)} points)`}
            </label>
          ))}
        </fieldset>
      ) : null}
      <Scene meta={d} jobId={jobId} />
    </div>
  );
}
