"use client";

import type { Job } from "@parallaxops/contracts";
import type { ReactNode } from "react";
import { bytes, num, seconds } from "../lib/format.ts";
import { HELP } from "../lib/metric-help.ts";

const STAGES: [string, string][] = [
  ["download_inputs", "Download images"],
  ["qc", "Image QC"],
  ["prepare_images", "Prepare (orient, resize)"],
  ["feature_extraction", "SIFT feature extraction"],
  ["feature_matching", "Matching + geometric verification"],
  ["sparse_mapping", "Sparse mapping + bundle adjustment"],
  ["metrics", "Geometry metrics"],
  ["export", "Export PLY, poses"],
  ["publish_artifacts", "Publish artifacts"],
];
// Only shown for runs that asked for it; otherwise it would sit "pending" forever.
const DENSE_STAGES: [string, string][] = [
  ["dense_patch_match", "Dense depth estimation (GPU PatchMatch)"],
  ["dense_fusion", "Dense fusion"],
];

export function Timeline({ job }: { job: Job }) {
  const byName = new Map(job.stages.map((s) => [s.name, s]));
  const failed = job.state === "FAILED" || job.state === "CANCELLED";
  const anyRunning = job.stages.some((s) => s.state === "RUNNING");
  return (
    <section aria-labelledby="tl-h" className="panel p-4">
      <div className="flex items-baseline justify-between">
        <h2 id="tl-h" className="text-sm font-semibold">
          Stage timeline
        </h2>
        <p className="num text-xs text-muted">
          queue wait {seconds(job.queueWaitSeconds)} · total {seconds(job.durationSeconds)}
        </p>
      </div>
      <ol className="mt-3 space-y-1.5">
        {(job.config.dense
          ? [...STAGES.slice(0, 8), ...DENSE_STAGES, ...STAGES.slice(8)]
          : STAGES
        ).map(([key, label]) => {
          const s = byName.get(key);
          const state = s?.state ?? "PENDING";
          const color =
            state === "DONE"
              ? "var(--good)"
              : state === "RUNNING"
                ? "var(--teal)"
                : failed && !s
                  ? "var(--line)"
                  : "var(--line)";
          return (
            <li key={key} className="flex items-center gap-3 text-sm">
              <span
                className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${state === "RUNNING" ? "pulse" : ""}`}
                style={{ background: color }}
                aria-hidden="true"
              />
              <span className={state === "PENDING" ? "text-muted" : ""}>{label}</span>
              <span className="sr-only">{state.toLowerCase()}</span>
              <span className="num ml-auto text-xs text-muted">
                {state === "DONE" ? seconds(s?.seconds) : state === "RUNNING" ? "running…" : ""}
              </span>
            </li>
          );
        })}
      </ol>
      {!anyRunning && job.state === "QUEUED" ? (
        <p className="mt-3 text-xs text-muted" aria-live="polite">
          Waiting for the heavy-job slot (one reconstruction runs at a time on this machine).
        </p>
      ) : null}
    </section>
  );
}

function Card({
  help,
  value,
  unit,
  hint,
}: {
  help: { label: string; source: string; meaning: string };
  value: ReactNode;
  unit?: string;
  hint?: ReactNode;
}) {
  return (
    <div className="panel p-3">
      <p className="text-xs text-muted">{help.label}</p>
      <p className="num mt-1 text-xl font-semibold">
        {value}
        {unit ? <span className="ml-1 text-xs font-normal text-muted">{unit}</span> : null}
      </p>
      {hint ? <p className="num text-xs text-muted">{hint}</p> : null}
      <details className="mt-1 text-xs">
        <summary className="cursor-pointer text-muted hover:text-ink">What is this?</summary>
        <p className="mt-1">{help.meaning}</p>
        <p className="mt-1 text-muted">Source: {help.source}</p>
      </details>
    </div>
  );
}

type M = {
  n_registered: number;
  n_input_images: number;
  registered_fraction: number;
  n_points3d: number;
  track_length_mean: number;
  reproj_error_px_mean: number;
  reproj_error_px_median: number;
  reproj_error_px_p90: number;
  triangulation_angle_deg_median: number;
};

export function Metrics({ job }: { job: Job }) {
  const m = job.metrics as M | null;
  if (!m) return null;
  const res = (job.resources ?? {}) as { peak_rss_bytes_process_tree?: number };
  const dense = (
    job.metrics as { dense?: { n_points: number; total_seconds: number; peak_vram_mib: number } }
  ).dense;
  const q = job.quality;
  const tone =
    q?.level === "good" ? "var(--good)" : q?.level === "poor" ? "var(--bad)" : "var(--warn)";
  return (
    <section aria-labelledby="m-h" className="space-y-3">
      <h2 id="m-h" className="text-sm font-semibold">
        Measured metrics
      </h2>
      {q ? (
        <div className="panel p-3" style={{ borderColor: tone }}>
          <p className="text-xs text-muted">Heuristic verdict (not accuracy)</p>
          <p className="mt-0.5 text-lg font-semibold uppercase" style={{ color: tone }}>
            {q.level}
          </p>
          <p className="text-xs">{q.reasons.join(" · ")}</p>
          {q.rubric ? (
            <details className="mt-1 text-xs">
              <summary className="cursor-pointer text-muted hover:text-ink">
                How is this decided?
              </summary>
              <p className="mt-1">{q.rubric}</p>
            </details>
          ) : null}
        </div>
      ) : null}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        <Card
          help={HELP.registered}
          value={`${m.n_registered}/${m.n_input_images}`}
          hint={`${(m.registered_fraction * 100).toFixed(0)}% of images`}
        />
        <Card help={HELP.points} value={num(m.n_points3d)} unit="points" />
        <Card
          help={HELP.reprojMean}
          value={m.reproj_error_px_mean.toFixed(3)}
          unit="px"
          hint={`p90 ${m.reproj_error_px_p90.toFixed(2)} px`}
        />
        <Card help={HELP.reprojMedian} value={m.reproj_error_px_median.toFixed(3)} unit="px" />
        <Card help={HELP.track} value={m.track_length_mean.toFixed(2)} unit="images/point" />
        <Card help={HELP.triAngle} value={m.triangulation_angle_deg_median.toFixed(1)} unit="deg" />
        <Card help={HELP.wall} value={seconds(job.durationSeconds)} />
        <Card help={HELP.queue} value={seconds(job.queueWaitSeconds)} />
        {dense ? (
          <>
            <Card help={HELP.denseCount} value={num(dense.n_points)} unit="points" />
            <Card help={HELP.denseTime} value={seconds(dense.total_seconds)} />
            <Card help={HELP.vram} value={`${num(dense.peak_vram_mib)} MiB`} />
          </>
        ) : null}
        {res.peak_rss_bytes_process_tree ? (
          <Card help={HELP.rss} value={bytes(res.peak_rss_bytes_process_tree)} />
        ) : null}
      </div>
    </section>
  );
}
