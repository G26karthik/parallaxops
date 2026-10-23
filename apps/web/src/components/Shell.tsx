"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useApiHealth } from "../lib/hooks.ts";
import { useTenantId } from "../lib/tenant.ts";
import { Wordmark } from "./Logo.tsx";

function HealthChip() {
  const { data, isError, isLoading } = useApiHealth();
  const ok = data?.status === "ready";
  const label = isLoading
    ? "Checking"
    : isError
      ? "API offline"
      : ok
        ? "All systems ready"
        : "Degraded";
  const tone = isLoading
    ? "var(--muted)"
    : ok
      ? "var(--good)"
      : isError
        ? "var(--bad)"
        : "var(--warn)";
  const detail = data
    ? Object.entries(data.components)
        .map(([k, v]) => `${k}: ${v}`)
        .join("\n")
    : "";
  return (
    <span className="chip" title={detail} role="status">
      <span
        className="inline-block h-2 w-2 rounded-full"
        style={{ background: tone }}
        aria-hidden="true"
      />
      {label}
    </span>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const tenant = useTenantId();
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="border-b border-line bg-panel/70 backdrop-blur">
        <div className="mx-auto flex max-w-[1240px] items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link href="/" aria-label="ParallaxOps home">
            <Wordmark />
          </Link>
          <nav aria-label="Primary" className="flex items-center gap-3 text-sm">
            <Link href="/" className="text-muted hover:text-ink">
              Projects
            </Link>
            <HealthChip />
            <span
              className="chip num hidden sm:inline-flex"
              title="Per-browser tenant id. Scoping, not authentication."
            >
              tenant {tenant ? tenant.slice(0, 8) : "…"}
            </span>
          </nav>
        </div>
      </header>
      <main id="main" className="mx-auto max-w-[1240px] px-4 py-6 sm:px-6">
        {children}
      </main>
      <footer className="mx-auto max-w-[1240px] px-4 pb-10 pt-4 text-xs text-muted sm:px-6">
        Sparse SfM results from real images. Reprojection error is a proxy, not accuracy; scale is
        arbitrary without an external reference.
      </footer>
    </>
  );
}
