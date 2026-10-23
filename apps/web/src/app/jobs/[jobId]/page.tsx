"use client";

import { isTerminal } from "@parallaxops/contracts";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Metrics, Timeline } from "../../../components/JobPanels.tsx";
import { DiagnosticPanel, ErrorBox, Skeleton, StateBadge } from "../../../components/ui.tsx";
import { Viewer } from "../../../components/Viewer.tsx";
import { unwrap } from "../../../lib/api.ts";
import { ago, bytes, seconds } from "../../../lib/format.ts";
import { useApi, useJob } from "../../../lib/hooks.ts";

function Artifacts({ jobId }: { jobId: string }) {
  const { api, tenantId } = useApi();
  const q = useQuery({
    queryKey: ["artifacts", tenantId, jobId],
    enabled: !!api,
    queryFn: () => unwrap(api!.GET("/v1/jobs/{jobId}/artifacts", { params: { path: { jobId } } })),
  });
  if (q.isLoading) return <Skeleton className="h-24" />;
  if (q.isError) return <ErrorBox error={q.error} />;
  const label: Record<string, string> = {
    points_ply: "Sparse point cloud (PLY)",
    points_preview: "Browser preview (binary)",
    cameras: "Camera poses (JSON)",
    report: "Report (JSON)",
    manifest: "Manifest with checksums (JSON)",
    qc_report: "Image QC report (JSON)",
    colmap_sparse: "COLMAP sparse model (zip)",
    render_oblique: "Render: oblique",
    render_top: "Render: top",
    render_front: "Render: front",
    render_metrics: "Render: metrics",
    engine_log: "Engine log",
  };
  return (
    <section aria-labelledby="a-h" className="space-y-2">
      <h2 id="a-h" className="text-sm font-semibold">
        Artifacts
      </h2>
      <div className="panel overflow-x-auto" tabIndex={0} role="region" aria-label="Artifact list">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Downloadable artifacts with checksums</caption>
          <tbody>
            {q.data?.map((a) => (
              <tr key={a.kind} className="border-t border-line first:border-t-0">
                <td className="px-3 py-2">
                  <a className="text-accent hover:underline" href={a.url} download={a.filename}>
                    {label[a.kind] ?? a.kind}
                  </a>
                </td>
                <td className="num px-3 py-2 text-xs text-muted">{bytes(a.bytes)}</td>
                <td
                  className="num hidden px-3 py-2 text-xs text-muted sm:table-cell"
                  title={`SHA-256 ${a.sha256}`}
                >
                  sha256 {a.sha256.slice(0, 12)}…
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function JobPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const { api, tenantId } = useApi();
  const qc = useQueryClient();
  const router = useRouter();
  const job = useJob(jobId);
  const cancel = useMutation({
    mutationFn: () => unwrap(api!.POST("/v1/jobs/{jobId}/cancel", { params: { path: { jobId } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["job", tenantId, jobId] }),
  });
  const rerun = useMutation({
    mutationFn: () =>
      unwrap(
        api!.POST("/v1/datasets/{datasetId}/jobs", {
          params: {
            path: { datasetId: job.data!.datasetId },
            header: { "idempotency-key": crypto.randomUUID() },
          },
          body: {
            config: job.data!.config,
            label: `re-run of ${job.data!.label ?? jobId.slice(0, 8)}`,
          },
        }),
      ),
    onSuccess: (j) => router.push(`/jobs/${j.id}`),
  });

  if (job.isError) return <ErrorBox error={job.error} />;
  const j = job.data;
  if (!j)
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-64" />
      </div>
    );
  const done = isTerminal(j.state);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <nav aria-label="Breadcrumb" className="text-xs text-muted">
            <Link href={`/projects/${j.projectId}`} className="hover:text-ink">
              Project
            </Link>{" "}
            / <span aria-current="page">Run {j.id.slice(0, 8)}</span>
          </nav>
          <h1 className="mt-1 flex flex-wrap items-center gap-3 text-2xl font-semibold tracking-tight">
            {j.label ?? "Reconstruction"}
            <span aria-live="polite">
              <StateBadge state={j.state} />
            </span>
          </h1>
          <p className="num mt-1 text-xs text-muted">
            created {ago(j.createdAt)} · {j.config.maxImageSize}px · {j.config.mapper} ·{" "}
            {j.config.cameraModel} · seed {j.config.seed}
          </p>
        </div>
        <div className="flex gap-2">
          {!done ? (
            <button
              type="button"
              className="btn btn-danger"
              disabled={cancel.isPending}
              onClick={() => cancel.mutate()}
            >
              {cancel.isPending ? "Cancelling…" : "Cancel run"}
            </button>
          ) : (
            <button
              type="button"
              className="btn"
              disabled={rerun.isPending}
              onClick={() => rerun.mutate()}
            >
              Re-run with same settings
            </button>
          )}
        </div>
      </div>
      {cancel.isError ? <ErrorBox error={cancel.error} /> : null}

      {j.diagnostic ? <DiagnosticPanel d={j.diagnostic} /> : null}
      {j.state === "CANCELLED" ? (
        <p role="status" className="panel border-warn/60 p-3 text-sm">
          This run was cancelled. The engine process tree was stopped and its scratch files removed.
        </p>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
        <div className="space-y-6">
          <Timeline job={j} />
          {j.attempts.length ? (
            <section aria-labelledby="att-h" className="panel p-4">
              <h2 id="att-h" className="text-sm font-semibold">
                Attempts
              </h2>
              <ul className="mt-2 space-y-1 text-xs">
                {j.attempts.map((a) => (
                  <li key={a.attemptNo} className="flex items-center justify-between gap-2">
                    <span className="num">#{a.attemptNo}</span>
                    <span className="chip">{a.state.toLowerCase()}</span>
                    <span className="num truncate text-muted" title={a.workerId ?? ""}>
                      {a.workerId ?? "-"}
                    </span>
                  </li>
                ))}
              </ul>
              {j.attempts.length > 1 && j.attempts.some((a) => a.failure) ? (
                <p className="mt-2 text-xs text-muted">
                  Earlier attempts that failed were retried automatically; each attempt is recorded
                  separately.
                </p>
              ) : null}
            </section>
          ) : null}
          {j.warnings.length ? (
            <section aria-label="Warnings" className="space-y-2">
              {j.warnings.map((w, i) => (
                <DiagnosticPanel key={`${w.code}-${i}`} d={w} tone="warn" />
              ))}
            </section>
          ) : null}
        </div>
        <div className="space-y-6">
          {j.state === "COMPLETED" ? <Viewer jobId={j.id} /> : null}
          {!j.diagnostic && !done ? (
            <div
              className="panel flex h-48 items-center justify-center text-sm text-muted"
              aria-live="polite"
            >
              The 3D scene appears here when reconstruction completes.
            </div>
          ) : null}
          <Metrics job={j} />
          {j.state === "COMPLETED" || j.artifacts.length ? <Artifacts jobId={j.id} /> : null}
        </div>
      </div>
    </div>
  );
}
