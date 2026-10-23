"use client";

import { ReconstructionConfigSchema } from "@parallaxops/contracts";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { UploadZone } from "../../../components/UploadZone.tsx";
import { EmptyState, ErrorBox, Field, Skeleton } from "../../../components/ui.tsx";
import { unwrap } from "../../../lib/api.ts";
import { bytes, displayName, num } from "../../../lib/format.ts";
import { useApi } from "../../../lib/hooks.ts";

interface QcImage {
  name: string;
  width: number;
  height: number;
  exif_focal_mm: number | null;
  camera: string | null;
  laplacian_var: number;
  mean_luma: number;
  overexposed_fraction: number;
  flags: string[];
}
interface QcReport {
  images: QcImage[];
  warnings: string[];
  note: string;
  total_megapixels: number;
}

const FLAG_TONE: Record<string, string> = {
  possibly_blurry: "var(--warn)",
  dark: "var(--warn)",
  overexposed: "var(--warn)",
  near_duplicate: "var(--warn)",
  exact_duplicate: "var(--bad)",
};

function Preflight({ report }: { report: QcReport }) {
  return (
    <div className="space-y-3">
      {report.warnings.length ? (
        <ul className="panel space-y-1 border-warn/60 p-3 text-sm" aria-label="Preflight warnings">
          {report.warnings.map((w) => (
            <li key={w}>• {w}</li>
          ))}
        </ul>
      ) : (
        <p className="panel p-3 text-sm text-good">No set-level warnings from the heuristics.</p>
      )}
      <div
        className="panel max-h-96 overflow-auto"
        tabIndex={0}
        role="region"
        aria-label="Per-image QC results"
      >
        <table className="w-full text-left text-xs">
          <caption className="sr-only">Per-image QC heuristics</caption>
          <thead className="sticky top-0 bg-panel text-muted">
            <tr>
              <th className="px-3 py-2 font-medium">Image</th>
              <th className="px-3 py-2 font-medium">Size</th>
              <th className="px-3 py-2 font-medium">Focal</th>
              <th
                className="px-3 py-2 font-medium"
                title="Variance of the Laplacian on a 1024 px copy. Higher is sharper. Heuristic."
              >
                Sharpness
              </th>
              <th className="px-3 py-2 font-medium">Flags</th>
            </tr>
          </thead>
          <tbody>
            {report.images.map((i) => (
              <tr key={i.name} className="border-t border-line">
                <td className="max-w-[12rem] truncate px-3 py-1.5" title={displayName(i.name)}>
                  {displayName(i.name)}
                </td>
                <td className="num px-3 py-1.5">
                  {i.width}×{i.height}
                </td>
                <td className="num px-3 py-1.5">
                  {i.exif_focal_mm ? `${i.exif_focal_mm} mm` : "no EXIF"}
                </td>
                <td className="num px-3 py-1.5">{num(i.laplacian_var)}</td>
                <td className="px-3 py-1.5">
                  {i.flags.length ? (
                    i.flags.map((f) => (
                      <span
                        key={f}
                        className="chip mr-1"
                        style={{ color: FLAG_TONE[f], borderColor: FLAG_TONE[f] }}
                      >
                        {f.replace("_", " ")}
                      </span>
                    ))
                  ) : (
                    <span className="text-muted">-</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted">{report.note}</p>
    </div>
  );
}

export default function DatasetPage() {
  const { datasetId } = useParams<{ datasetId: string }>();
  const { api, tenantId } = useApi();
  const qc = useQueryClient();
  const router = useRouter();
  const [cfg, setCfg] = useState(() => ReconstructionConfigSchema.parse({}));
  const [label, setLabel] = useState("");

  const dataset = useQuery({
    queryKey: ["dataset", tenantId, datasetId],
    enabled: !!api,
    queryFn: () =>
      unwrap(api!.GET("/v1/datasets/{datasetId}", { params: { path: { datasetId } } })),
    refetchInterval: (q) => (q.state.data?.preflight.status === "RUNNING" ? 1500 : false),
  });
  const images = useQuery({
    queryKey: ["images", tenantId, datasetId],
    enabled: !!api,
    queryFn: () =>
      unwrap(api!.GET("/v1/datasets/{datasetId}/images", { params: { path: { datasetId } } })),
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["dataset"] });
    void qc.invalidateQueries({ queryKey: ["images"] });
  };
  const preflight = useMutation({
    mutationFn: () =>
      unwrap(api!.POST("/v1/datasets/{datasetId}/preflight", { params: { path: { datasetId } } })),
    onSuccess: refresh,
  });
  const start = useMutation({
    mutationFn: () =>
      unwrap(
        api!.POST("/v1/datasets/{datasetId}/jobs", {
          params: {
            path: { datasetId },
            header: { "idempotency-key": crypto.randomUUID() },
          },
          body: { config: cfg, label: label.trim() || undefined },
        }),
      ),
    onSuccess: (j) => router.push(`/jobs/${j.id}`),
  });

  if (dataset.isError) return <ErrorBox error={dataset.error} />;
  const d = dataset.data;
  const uploaded = images.data?.filter((i) => i.status === "UPLOADED") ?? [];
  const pf = d?.preflight;
  const report = pf?.status === "DONE" ? (pf.report as unknown as QcReport) : null;

  return (
    <div className="space-y-6">
      <div>
        <nav aria-label="Breadcrumb" className="text-xs text-muted">
          <Link href="/" className="hover:text-ink">
            Projects
          </Link>
          {d ? (
            <>
              {" / "}
              <Link href={`/projects/${d.projectId}`} className="hover:text-ink">
                Project
              </Link>
            </>
          ) : null}{" "}
          / <span aria-current="page">{d?.name ?? "…"}</span>
        </nav>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">
          {d?.name ?? <Skeleton className="inline-block h-7 w-48" />}
        </h1>
        <p className="num mt-1 text-xs text-muted">
          {d ? `${d.imageCount} verified images · ${bytes(d.totalBytes)}` : ""}
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <div className="space-y-6">
          <section aria-labelledby="up-h" className="space-y-3">
            <h2 id="up-h" className="text-lg font-semibold">
              1 · Upload and inspect
            </h2>
            {api ? (
              <UploadZone api={api} datasetId={datasetId} onFinished={refresh} />
            ) : (
              <Skeleton className="h-32" />
            )}
            {images.data?.length ? (
              <div
                className="panel max-h-64 overflow-auto"
                tabIndex={0}
                role="region"
                aria-label="Images in this dataset"
              >
                <table className="w-full text-left text-xs">
                  <caption className="sr-only">Images in this dataset</caption>
                  <tbody>
                    {images.data.map((i) => (
                      <tr key={i.id} className="border-t border-line first:border-t-0">
                        <td className="max-w-[16rem] truncate px-3 py-1.5" title={i.filename}>
                          {i.filename}
                        </td>
                        <td className="num px-3 py-1.5 text-muted">{bytes(i.sizeBytes)}</td>
                        <td className="num px-3 py-1.5 text-muted" title={i.sha256}>
                          {i.sha256.slice(0, 10)}…
                        </td>
                        <td className="px-3 py-1.5">
                          <span className="chip">{i.status.toLowerCase()}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </section>

          <section aria-labelledby="pf-h" className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h2 id="pf-h" className="text-lg font-semibold">
                2 · Preflight
              </h2>
              <button
                type="button"
                className="btn"
                disabled={!uploaded.length || preflight.isPending || pf?.status === "RUNNING"}
                onClick={() => preflight.mutate()}
              >
                {pf?.status === "RUNNING" ? "Checking…" : "Run capture checks"}
              </button>
            </div>
            {preflight.isError ? <ErrorBox error={preflight.error} /> : null}
            {pf?.status === "RUNNING" ? (
              <div aria-live="polite" className="panel space-y-2 p-4">
                <p className="text-sm">Worker is downloading and analysing images…</p>
                <Skeleton className="h-3 w-2/3" />
              </div>
            ) : pf?.status === "FAILED" && pf.error ? (
              <div role="alert" className="panel border-bad/60 p-4 text-sm">
                <p className="font-medium text-bad">{pf.error.code}</p>
                <p>{pf.error.message}</p>
                <p className="mt-1 text-accent">{pf.error.advice}</p>
              </div>
            ) : report ? (
              <Preflight report={report} />
            ) : (
              <EmptyState title="Not checked yet">
                Preflight flags blurry, dark, overexposed and duplicate images before you spend
                compute. It is a heuristic, not a guarantee.
              </EmptyState>
            )}
          </section>
        </div>

        <aside aria-labelledby="run-h" className="space-y-3 lg:sticky lg:top-4 lg:self-start">
          <h2 id="run-h" className="text-lg font-semibold">
            3 · Reconstruct
          </h2>
          <form
            className="panel space-y-3 p-4"
            onSubmit={(e) => {
              e.preventDefault();
              start.mutate();
            }}
          >
            <Field label="Run label (optional)">
              <input
                className="input"
                value={label}
                maxLength={80}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="e.g. baseline"
              />
            </Field>
            <Field
              label={`Working resolution: ${cfg.maxImageSize}px`}
              hint="Longest image side used by SfM. Higher is slower and uses more memory."
            >
              <input
                type="range"
                min={480}
                max={3200}
                step={160}
                value={cfg.maxImageSize}
                className="w-full accent-[var(--accent)]"
                onChange={(e) => setCfg({ ...cfg, maxImageSize: Number(e.target.value) })}
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Mapper">
                <select
                  className="input"
                  value={cfg.mapper}
                  onChange={(e) => setCfg({ ...cfg, mapper: e.target.value as typeof cfg.mapper })}
                >
                  <option value="incremental">Incremental</option>
                  <option value="global">Global (GLOMAP)</option>
                </select>
              </Field>
              <Field label="Matching">
                <select
                  className="input"
                  value={cfg.matching}
                  onChange={(e) =>
                    setCfg({ ...cfg, matching: e.target.value as typeof cfg.matching })
                  }
                >
                  <option value="auto">Auto</option>
                  <option value="exhaustive">Exhaustive</option>
                  <option value="sequential">Sequential</option>
                </select>
              </Field>
              <Field label="Camera model">
                <select
                  className="input"
                  value={cfg.cameraModel}
                  onChange={(e) =>
                    setCfg({ ...cfg, cameraModel: e.target.value as typeof cfg.cameraModel })
                  }
                >
                  <option>SIMPLE_RADIAL</option>
                  <option>RADIAL</option>
                  <option>OPENCV</option>
                  <option>SIMPLE_PINHOLE</option>
                </select>
              </Field>
              <Field label="Features / image">
                <select
                  className="input"
                  value={cfg.maxNumFeatures}
                  onChange={(e) => setCfg({ ...cfg, maxNumFeatures: Number(e.target.value) })}
                >
                  {[4096, 8192, 12288, 16384].map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <button
              className="btn btn-primary w-full justify-center"
              disabled={uploaded.length < 5 || start.isPending}
            >
              {start.isPending ? "Submitting…" : "Start reconstruction"}
            </button>
            {uploaded.length < 5 ? (
              <p className="text-xs text-muted">
                Needs at least 5 verified images (15 to 35 recommended).
              </p>
            ) : (
              <p className="text-xs text-muted">
                Measured on the dev laptop (CPU only): 23 images at 1600 px took about 1.5 minutes.
                Yours will differ.
              </p>
            )}
            {start.isError ? <ErrorBox error={start.error} /> : null}
          </form>
        </aside>
      </div>
    </div>
  );
}
