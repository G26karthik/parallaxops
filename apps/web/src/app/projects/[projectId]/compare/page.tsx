"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { EmptyState, ErrorBox, Skeleton } from "../../../../components/ui.tsx";
import { unwrap } from "../../../../lib/api.ts";
import { num, seconds } from "../../../../lib/format.ts";
import { useApi } from "../../../../lib/hooks.ts";

type M = {
  n_registered: number;
  n_input_images: number;
  registered_fraction: number;
  n_points3d: number;
  track_length_mean: number;
  reproj_error_px_mean: number;
  triangulation_angle_deg_median: number;
};

const ROWS: {
  key: string;
  label: string;
  unit: string;
  get: (m: M, d: number | null) => number | null;
  fmt: (v: number) => string;
  better: "high" | "low" | "none";
}[] = [
  {
    key: "reg",
    label: "Registered fraction",
    unit: "",
    get: (m) => m.registered_fraction,
    fmt: (v) => `${(v * 100).toFixed(0)}%`,
    better: "high",
  },
  {
    key: "pts",
    label: "Sparse points",
    unit: "",
    get: (m) => m.n_points3d,
    fmt: (v) => num(v),
    better: "none",
  },
  {
    key: "err",
    label: "Mean reprojection error",
    unit: "px",
    get: (m) => m.reproj_error_px_mean,
    fmt: (v) => v.toFixed(3),
    better: "low",
  },
  {
    key: "trk",
    label: "Mean track length",
    unit: "",
    get: (m) => m.track_length_mean,
    fmt: (v) => v.toFixed(2),
    better: "high",
  },
  {
    key: "tri",
    label: "Median triangulation angle",
    unit: "deg",
    get: (m) => m.triangulation_angle_deg_median,
    fmt: (v) => v.toFixed(1),
    better: "high",
  },
  {
    key: "dur",
    label: "Wall clock",
    unit: "",
    get: (_m, d) => d,
    fmt: (v) => seconds(v),
    better: "low",
  },
];

export default function ComparePage() {
  const { projectId } = useParams<{ projectId: string }>();
  const { api, tenantId } = useApi();
  const [picked, setPicked] = useState<string[]>([]);
  const jobs = useQuery({
    queryKey: ["jobs", tenantId, projectId],
    enabled: !!api,
    queryFn: () =>
      unwrap(api!.GET("/v1/projects/{projectId}/jobs", { params: { path: { projectId } } })),
  });
  const done = useMemo(
    () => jobs.data?.filter((j) => j.state === "COMPLETED" && j.metrics) ?? [],
    [jobs.data],
  );
  const chosen = useMemo(() => {
    const ids = picked.length ? picked : done.slice(0, 3).map((j) => j.id);
    return done.filter((j) => ids.includes(j.id));
  }, [picked, done]);

  if (jobs.isError) return <ErrorBox error={jobs.error} />;
  const toggle = (id: string) =>
    setPicked((cur) => {
      const base = cur.length ? cur : chosen.map((j) => j.id);
      return base.includes(id) ? base.filter((x) => x !== id) : [...base, id];
    });

  return (
    <div className="space-y-6">
      <div>
        <nav aria-label="Breadcrumb" className="text-xs text-muted">
          <Link href={`/projects/${projectId}`} className="hover:text-ink">
            Project
          </Link>{" "}
          / <span aria-current="page">Compare runs</span>
        </nav>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">Compare runs</h1>
      </div>

      {jobs.isLoading ? (
        <Skeleton className="h-40" />
      ) : done.length < 2 ? (
        <EmptyState title="Need two completed runs">
          Complete at least two reconstructions in this project to compare them.
        </EmptyState>
      ) : (
        <>
          <fieldset className="panel p-3">
            <legend className="px-1 text-xs text-muted">Runs to compare</legend>
            <div className="flex flex-wrap gap-3 text-sm">
              {done.map((j) => (
                <label key={j.id} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={chosen.some((c) => c.id === j.id)}
                    onChange={() => toggle(j.id)}
                  />
                  {j.label ?? j.id.slice(0, 8)}
                  <span className="num text-xs text-muted">
                    {j.config.maxImageSize}px/{j.config.mapper}
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          <div
            className="panel overflow-x-auto"
            tabIndex={0}
            role="region"
            aria-label="Metric comparison"
          >
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Metric comparison between runs</caption>
              <thead className="text-xs text-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">Metric</th>
                  {chosen.map((j) => (
                    <th key={j.id} className="px-3 py-2 font-medium">
                      <Link href={`/jobs/${j.id}`} className="text-accent hover:underline">
                        {j.label ?? j.id.slice(0, 8)}
                      </Link>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ROWS.map((r) => {
                  const vals = chosen.map((j) =>
                    r.get(j.metrics as unknown as M, j.durationSeconds),
                  );
                  const nums = vals.filter((v): v is number => v !== null);
                  const best =
                    r.better === "none" || !nums.length
                      ? null
                      : r.better === "high"
                        ? Math.max(...nums)
                        : Math.min(...nums);
                  const max = Math.max(...nums, 1e-9);
                  return (
                    <tr key={r.key} className="border-t border-line">
                      <th scope="row" className="px-3 py-2 font-normal">
                        {r.label}
                        {r.unit ? (
                          <span className="ml-1 text-xs text-muted">({r.unit})</span>
                        ) : null}
                      </th>
                      {vals.map((v, i) => (
                        <td key={chosen[i]?.id} className="num px-3 py-2">
                          {v === null ? (
                            "-"
                          ) : (
                            <div>
                              <span
                                style={{
                                  color: best !== null && v === best ? "var(--good)" : undefined,
                                }}
                              >
                                {r.fmt(v)}
                              </span>
                              <div className="mt-1 h-1 rounded bg-line" aria-hidden="true">
                                <div
                                  className="h-1 rounded bg-teal"
                                  style={{ width: `${(v / max) * 100}%` }}
                                />
                              </div>
                            </div>
                          )}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="panel p-3 text-xs text-muted">
            Read with care: runs are not bit-reproducible (multithreaded matching and bundle
            adjustment), so a difference smaller than the run-to-run spread is inconclusive. On the
            ETH3D terrace scene the same configuration produced between 5,432 and 5,449 points
            across four runs. More points does not mean better geometry, and low reprojection error
            is not proof of accuracy. Highlighted values are only the numerically best, not a
            verdict.
          </p>
        </>
      )}
    </div>
  );
}
