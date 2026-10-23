"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { unwrap } from "../lib/api.ts";
import { num } from "../lib/format.ts";
import { useApi } from "../lib/hooks.ts";
import { SCENE_NOTE } from "../lib/metric-help.ts";
import {
  type CameraPose,
  type Framing,
  frame,
  type Preview,
  parseCameras,
  parsePreview,
  ramp,
  toDisplay,
} from "../lib/pointcloud.ts";
import { Skeleton } from "./ui.tsx";

type ColorMode = "rgb" | "depth" | "flat";

interface SceneData {
  preview: Preview;
  cams: CameraPose[];
  framing: Framing;
}

function useScene(jobId: string) {
  const { api, tenantId } = useApi();
  return useQuery({
    queryKey: ["scene", tenantId, jobId],
    enabled: !!api,
    staleTime: 10 * 60_000,
    queryFn: async (): Promise<SceneData> => {
      const s = await unwrap(api!.GET("/v1/jobs/{jobId}/scene", { params: { path: { jobId } } }));
      const [pBuf, cJson] = await Promise.all([
        fetch(s.pointsUrl).then((r) => {
          if (!r.ok) throw new Error(`points download failed (${r.status})`);
          return r.arrayBuffer();
        }),
        fetch(s.camerasUrl).then((r) => {
          if (!r.ok) throw new Error(`cameras download failed (${r.status})`);
          return r.json();
        }),
      ]);
      const preview = parsePreview(pBuf);
      const cams = parseCameras(cJson);
      return { preview, cams, framing: frame(preview, cams) };
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

function Scene({ data, jobId }: { data: SceneData; jobId: string }) {
  const host = useRef<HTMLDivElement>(null);
  const ctl = useRef<{
    points?: THREE.Points;
    frustums?: THREE.LineSegments;
    axes?: THREE.AxesHelper;
    material?: THREE.PointsMaterial;
    colors?: THREE.BufferAttribute;
    render?: () => void;
    reset?: () => void;
    shot?: () => void;
    goTo?: (i: number) => void;
  }>({});
  const [mode, setMode] = useState<ColorMode>("rgb");
  const [size, setSize] = useState(2.5);
  const [showCams, setShowCams] = useState(true);
  const [showAxes, setShowAxes] = useState(false);
  const [trim, setTrim] = useState(true);
  const [webgl, setWebgl] = useState(true);
  const { preview, cams, framing } = data;

  // Build the scene once per dataset; display settings are applied by the effects below.
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
    const style = getComputedStyle(document.documentElement);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(new THREE.Color(style.getPropertyValue("--bg").trim() || "#0b0d10"));
    el.appendChild(renderer.domElement);
    renderer.domElement.tabIndex = 0;
    renderer.domElement.setAttribute("role", "img");
    renderer.domElement.setAttribute(
      "aria-label",
      `3D sparse point cloud with ${preview.count} points and ${cams.length} cameras. Arrow keys orbit, plus and minus zoom.`,
    );

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(50, 1, 0.01, 100);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false;
    controls.listenToKeyEvents(renderer.domElement);
    controls.keyPanSpeed = 12;

    const n = preview.count;
    const pos = new Float32Array(n * 3);
    const depth = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const p = toDisplay(
        [preview.xyz[3 * i] ?? 0, preview.xyz[3 * i + 1] ?? 0, preview.xyz[3 * i + 2] ?? 0],
        framing,
      );
      pos.set(p, 3 * i);
      depth[i] = Math.hypot(p[0], p[1], p[2]);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    const colors = new THREE.BufferAttribute(new Float32Array(n * 3), 3);
    geo.setAttribute("color", colors);
    const material = new THREE.PointsMaterial({
      size: 2.5,
      sizeAttenuation: false,
      vertexColors: true,
    });
    const points = new THREE.Points(geo, material);
    scene.add(points);
    const frustums = buildFrustums(cams, framing);
    scene.add(frustums);
    const axes = new THREE.AxesHelper(0.5);
    axes.visible = false;
    scene.add(axes);
    Object.assign(ctl.current, { points, frustums, axes, material, colors });
    (ctl.current as { depth?: Float32Array }).depth = depth;

    const render = () => renderer.render(scene, camera);
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
    controls.addEventListener("change", render);
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();
    home();

    ctl.current.render = render;
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
      if (!c) return;
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

    return () => {
      ro.disconnect();
      controls.dispose();
      geo.dispose();
      material.dispose();
      frustums.geometry.dispose();
      (frustums.material as THREE.Material).dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [preview, cams, framing, jobId]);

  // Apply settings.
  useEffect(() => {
    const c = ctl.current;
    if (!c.colors || !c.points) return;
    const n = preview.count;
    const depth = (c as unknown as { depth: Float32Array }).depth;
    const maxD = framing.trimDistance / framing.radius;
    const arr = c.colors.array as Float32Array;
    for (let i = 0; i < n; i++) {
      const far = depth[i]! > maxD;
      let rgb: [number, number, number];
      if (mode === "rgb")
        rgb = [
          (preview.rgb[3 * i] ?? 0) / 255,
          (preview.rgb[3 * i + 1] ?? 0) / 255,
          (preview.rgb[3 * i + 2] ?? 0) / 255,
        ];
      else if (mode === "depth") rgb = ramp(Math.min(depth[i]! / 1.6, 1));
      else rgb = [0.3, 0.76, 0.77];
      // Hidden far points are collapsed to the clear colour instead of rebuilding the geometry.
      arr.set(trim && far ? [0, 0, 0] : rgb, 3 * i);
    }
    c.colors.needsUpdate = true;
    c.render?.();
  }, [mode, trim, preview, framing]);
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
          {framing.farCount ? (
            <span className="num text-muted">({num(framing.farCount)})</span>
          ) : null}
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
      <p className="border-t border-line px-3 py-2 text-xs text-muted">
        <span className="num">{num(preview.count)}</span> points. Drag to orbit, scroll to zoom,
        right-drag to pan; arrow keys orbit when focused. {SCENE_NOTE}
      </p>
    </div>
  );
}

export function Viewer({ jobId }: { jobId: string }) {
  const q = useScene(jobId);
  if (q.isLoading) return <Skeleton className="h-[460px]" />;
  if (q.isError)
    return (
      <div role="alert" className="panel p-4 text-sm text-bad">
        Could not load the 3D scene: {(q.error as Error).message}
      </div>
    );
  return q.data ? <Scene data={q.data} jobId={jobId} /> : null;
}
