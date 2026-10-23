"use client";

import type { Diagnostic, JobState } from "@parallaxops/contracts";
import type { ReactNode } from "react";

const TONE: Record<string, string> = {
  QUEUED: "var(--muted)",
  CREATED: "var(--muted)",
  UPLOADING: "var(--muted)",
  PREPROCESSING: "var(--teal)",
  RECONSTRUCTING: "var(--teal)",
  EVALUATING: "var(--teal)",
  COMPLETED: "var(--good)",
  FAILED: "var(--bad)",
  CANCELLED: "var(--warn)",
};

export function StateBadge({ state }: { state: JobState }) {
  const live = ["PREPROCESSING", "RECONSTRUCTING", "EVALUATING"].includes(state);
  return (
    <span
      className="chip num uppercase tracking-wide"
      style={{ color: TONE[state], borderColor: TONE[state] }}
    >
      <span
        className={`inline-block h-2 w-2 rounded-full ${live ? "pulse" : ""}`}
        style={{ background: TONE[state] }}
        aria-hidden="true"
      />
      {state}
    </span>
  );
}

export function Skeleton({ className = "h-6 w-full" }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />;
}

export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="panel flex flex-col items-center gap-2 px-6 py-10 text-center">
      <p className="text-base font-medium">{title}</p>
      {children ? <p className="max-w-md text-sm text-muted">{children}</p> : null}
      {action}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const e = error as { message?: string; requestId?: string; code?: string };
  return (
    <div role="alert" className="panel border-bad/60 p-4 text-sm">
      <p className="font-medium text-bad">{e.code ?? "Error"}</p>
      <p className="mt-1">{e.message ?? "Something went wrong."}</p>
      {e.requestId ? <p className="num mt-2 text-xs text-muted">request {e.requestId}</p> : null}
    </div>
  );
}

/** Failure panel: typed code, what happened, and what to do about it. */
export function DiagnosticPanel({ d, tone = "bad" }: { d: Diagnostic; tone?: "bad" | "warn" }) {
  return (
    <section
      className="panel p-4"
      style={{ borderColor: `var(--${tone})` }}
      aria-label={`${d.code} diagnostic`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span
          className="chip num"
          style={{ color: `var(--${tone})`, borderColor: `var(--${tone})` }}
        >
          {d.code}
        </span>
        {d.stage ? <span className="chip">stage: {d.stage}</span> : null}
      </div>
      <p className="mt-2 text-sm">{d.message}</p>
      <p className="mt-2 text-sm">
        <span className="font-medium text-accent">What to do: </span>
        {d.advice}
      </p>
    </section>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium">{label}</span>
      {children}
      {hint ? <span className="mt-1 block text-xs text-muted">{hint}</span> : null}
    </label>
  );
}
