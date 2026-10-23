"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { type FormEvent, useState } from "react";
import { EmptyState, ErrorBox, Skeleton, StateBadge } from "../../../components/ui.tsx";
import { unwrap } from "../../../lib/api.ts";
import { ago, bytes, num, seconds } from "../../../lib/format.ts";
import { useApi } from "../../../lib/hooks.ts";

export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const { api, tenantId } = useApi();
  const qc = useQueryClient();
  const [name, setName] = useState("");

  const project = useQuery({
    queryKey: ["project", tenantId, projectId],
    enabled: !!api,
    queryFn: () =>
      unwrap(api!.GET("/v1/projects/{projectId}", { params: { path: { projectId } } })),
  });
  const datasets = useQuery({
    queryKey: ["datasets", tenantId, projectId],
    enabled: !!api,
    queryFn: () =>
      unwrap(api!.GET("/v1/projects/{projectId}/datasets", { params: { path: { projectId } } })),
  });
  const jobs = useQuery({
    queryKey: ["jobs", tenantId, projectId],
    enabled: !!api,
    refetchInterval: 3000,
    queryFn: () =>
      unwrap(api!.GET("/v1/projects/{projectId}/jobs", { params: { path: { projectId } } })),
  });
  const create = useMutation({
    mutationFn: (n: string) =>
      unwrap(
        api!.POST("/v1/projects/{projectId}/datasets", {
          params: { path: { projectId } },
          body: { name: n },
        }),
      ),
    onSuccess: () => {
      setName("");
      return qc.invalidateQueries({ queryKey: ["datasets"] });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (name.trim()) create.mutate(name.trim());
  };

  if (project.isError) return <ErrorBox error={project.error} />;
  const done = jobs.data?.filter((j) => j.state === "COMPLETED") ?? [];

  return (
    <div className="space-y-8">
      <div>
        <nav aria-label="Breadcrumb" className="text-xs text-muted">
          <Link href="/" className="hover:text-ink">
            Projects
          </Link>{" "}
          / <span aria-current="page">{project.data?.name ?? "…"}</span>
        </nav>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">
          {project.data?.name ?? <Skeleton className="inline-block h-7 w-48" />}
        </h1>
      </div>

      <section aria-labelledby="ds-h" className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h2 id="ds-h" className="text-lg font-semibold">
            Datasets
          </h2>
          <form onSubmit={submit} className="flex gap-2" aria-label="Create dataset">
            <label htmlFor="dname" className="sr-only">
              Dataset name
            </label>
            <input
              id="dname"
              className="input w-56"
              placeholder="e.g. Terrace, morning"
              value={name}
              maxLength={80}
              onChange={(e) => setName(e.target.value)}
            />
            <button className="btn btn-primary" disabled={!name.trim() || create.isPending}>
              Add dataset
            </button>
          </form>
        </div>
        {datasets.isLoading ? (
          <Skeleton className="h-20" />
        ) : datasets.data?.length ? (
          <ul className="grid gap-3 sm:grid-cols-2">
            {datasets.data.map((d) => (
              <li key={d.id}>
                <Link
                  href={`/datasets/${d.id}`}
                  className="panel block p-4 transition-colors hover:border-accent"
                >
                  <p className="font-medium">{d.name}</p>
                  <p className="num mt-1 text-xs text-muted">
                    {d.imageCount} images · {bytes(d.totalBytes)} · preflight{" "}
                    {d.preflight.status.toLowerCase()}
                  </p>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="No datasets">
            Add a dataset, then upload overlapping photographs of one scene.
          </EmptyState>
        )}
      </section>

      <section aria-labelledby="runs-h" className="space-y-3">
        <div className="flex items-end justify-between">
          <h2 id="runs-h" className="text-lg font-semibold">
            Reconstruction runs
          </h2>
          {done.length >= 2 ? (
            <Link className="btn" href={`/projects/${projectId}/compare`}>
              Compare {done.length} completed runs
            </Link>
          ) : null}
        </div>
        {jobs.isLoading ? (
          <Skeleton className="h-24" />
        ) : jobs.data?.length ? (
          <div
            className="panel overflow-x-auto"
            tabIndex={0}
            role="region"
            aria-label="Reconstruction runs"
          >
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Reconstruction runs in this project</caption>
              <thead className="text-xs text-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">Run</th>
                  <th className="px-3 py-2 font-medium">State</th>
                  <th className="px-3 py-2 font-medium">Registered</th>
                  <th className="px-3 py-2 font-medium">Points</th>
                  <th className="px-3 py-2 font-medium">Duration</th>
                  <th className="px-3 py-2 font-medium">Created</th>
                </tr>
              </thead>
              <tbody>
                {jobs.data.map((j) => {
                  const m = (j.metrics ?? {}) as {
                    n_registered?: number;
                    n_input_images?: number;
                    n_points3d?: number;
                  };
                  return (
                    <tr key={j.id} className="border-t border-line">
                      <td className="px-3 py-2">
                        <Link className="text-accent hover:underline" href={`/jobs/${j.id}`}>
                          {j.label ?? j.id.slice(0, 8)}
                        </Link>
                      </td>
                      <td className="px-3 py-2">
                        <StateBadge state={j.state} />
                      </td>
                      <td className="num px-3 py-2">
                        {m.n_registered !== undefined
                          ? `${m.n_registered}/${m.n_input_images}`
                          : "-"}
                      </td>
                      <td className="num px-3 py-2">{num(m.n_points3d)}</td>
                      <td className="num px-3 py-2">{seconds(j.durationSeconds)}</td>
                      <td className="num px-3 py-2 text-muted">{ago(j.createdAt)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="No runs yet">
            Upload images to a dataset, then start a reconstruction.
          </EmptyState>
        )}
      </section>
    </div>
  );
}
