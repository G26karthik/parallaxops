"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { type FormEvent, useState } from "react";
import { EmptyState, ErrorBox, Skeleton } from "../components/ui.tsx";
import { unwrap } from "../lib/api.ts";
import { ago } from "../lib/format.ts";
import { useApi } from "../lib/hooks.ts";

const STEPS = [
  ["1", "Upload", "15 to 60 overlapping photos. Checksummed, size-checked, deduplicated."],
  ["2", "Preflight", "Blur, exposure and duplicate heuristics before you spend compute."],
  ["3", "Reconstruct", "Real COLMAP structure-from-motion on a supervised, cancellable worker."],
  ["4", "Inspect", "Interactive point cloud with measured metrics and plain-language caveats."],
];

export default function Home() {
  const { api, tenantId } = useApi();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const projects = useQuery({
    queryKey: ["projects", tenantId],
    enabled: !!api,
    queryFn: () => unwrap(api!.GET("/v1/projects")),
  });
  const create = useMutation({
    mutationFn: (n: string) => unwrap(api!.POST("/v1/projects", { body: { name: n } })),
    onSuccess: () => {
      setName("");
      return qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (name.trim()) create.mutate(name.trim());
  };

  return (
    <div className="space-y-8">
      <section aria-labelledby="hero" className="grid gap-6 lg:grid-cols-[1.2fr_1fr] lg:items-end">
        <div>
          <p className="num text-xs uppercase tracking-[0.18em] text-accent">
            Distributed 3D reconstruction
          </p>
          <h1
            id="hero"
            className="mt-2 text-3xl font-semibold leading-tight tracking-tight sm:text-4xl"
          >
            Photos in. Geometry out.
            <br />
            <span className="text-muted">Honest numbers, every time.</span>
          </h1>
          <p className="mt-3 max-w-xl text-sm text-muted">
            ParallaxOps runs real structure-from-motion on your photographs, shows what it could and
            could not reconstruct, and never presents a proxy metric as accuracy.
          </p>
        </div>
        <ol className="grid grid-cols-2 gap-2 text-sm">
          {STEPS.map(([n, t, d]) => (
            <li key={n} className="panel p-3">
              <span className="num text-xs text-accent">{n}</span>
              <p className="font-medium">{t}</p>
              <p className="mt-0.5 text-xs text-muted">{d}</p>
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="projects-h" className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h2 id="projects-h" className="text-lg font-semibold">
            Projects
          </h2>
          <form onSubmit={submit} className="flex gap-2" aria-label="Create project">
            <label htmlFor="pname" className="sr-only">
              Project name
            </label>
            <input
              id="pname"
              className="input w-56"
              placeholder="New project name"
              value={name}
              maxLength={80}
              onChange={(e) => setName(e.target.value)}
            />
            <button className="btn btn-primary" disabled={!name.trim() || create.isPending || !api}>
              Create project
            </button>
          </form>
        </div>
        {create.isError ? <ErrorBox error={create.error} /> : null}
        {projects.isLoading || !api ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="panel space-y-3 p-4">
                <Skeleton className="h-5 w-2/3" />
                <Skeleton className="h-4 w-1/3" />
              </div>
            ))}
          </div>
        ) : projects.isError ? (
          <ErrorBox error={projects.error} />
        ) : projects.data?.length ? (
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {projects.data.map((p) => (
              <li key={p.id}>
                <Link
                  href={`/projects/${p.id}`}
                  className="panel block p-4 transition-colors hover:border-accent"
                >
                  <p className="font-medium">{p.name}</p>
                  <p className="num mt-1 text-xs text-muted">created {ago(p.createdAt)}</p>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="No projects yet">
            Create a project to group datasets and compare reconstructions. A project can hold
            several datasets and many runs of the same scene.
          </EmptyState>
        )}
      </section>
    </div>
  );
}
