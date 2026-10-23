import { Counter, collectDefaultMetrics, Gauge, Histogram, Registry } from "prom-client";

/** One registry per app instance so tests never share global state. */
export function createMetrics() {
  const registry = new Registry();
  collectDefaultMetrics({ register: registry, prefix: "parallaxops_api_" });
  const seconds = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10];
  const long = [1, 5, 15, 30, 60, 120, 300, 600, 1200, 3600];
  return {
    registry,
    httpDuration: new Histogram({
      name: "parallaxops_http_request_duration_seconds",
      help: "API request latency",
      labelNames: ["method", "route", "status"] as const,
      buckets: seconds,
      registers: [registry],
    }),
    jobsCreated: new Counter({
      name: "parallaxops_jobs_created_total",
      help: "Jobs created",
      labelNames: ["replay"] as const,
      registers: [registry],
    }),
    transitions: new Counter({
      name: "parallaxops_job_transitions_total",
      help: "Job state transitions applied",
      labelNames: ["to"] as const,
      registers: [registry],
    }),
    rejectedTransitions: new Counter({
      name: "parallaxops_job_transitions_rejected_total",
      help: "Worker events whose state change was not a valid transition",
      registers: [registry],
    }),
    jobDuration: new Histogram({
      name: "parallaxops_job_duration_seconds",
      help: "Job wall clock from first attempt start to terminal state",
      labelNames: ["outcome"] as const,
      buckets: long,
      registers: [registry],
    }),
    queueWait: new Histogram({
      name: "parallaxops_job_queue_wait_seconds",
      help: "Time from QUEUED to first attempt start",
      buckets: long,
      registers: [registry],
    }),
    stageDuration: new Histogram({
      name: "parallaxops_stage_duration_seconds",
      help: "Reconstruction stage duration reported by the worker",
      labelNames: ["stage"] as const,
      buckets: long,
      registers: [registry],
    }),
    artifactBytes: new Histogram({
      name: "parallaxops_artifact_bytes",
      help: "Published artifact sizes",
      labelNames: ["kind"] as const,
      buckets: [1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9],
      registers: [registry],
    }),
    uploadRejected: new Counter({
      name: "parallaxops_upload_rejected_total",
      help: "Uploads rejected after completion checks",
      labelNames: ["reason"] as const,
      registers: [registry],
    }),
    workflowStartFailures: new Counter({
      name: "parallaxops_workflow_start_failures_total",
      help: "Workflow starts that failed and were left to the reconciler",
      registers: [registry],
    }),
    cleanupRemoved: new Counter({
      name: "parallaxops_cleanup_removed_total",
      help: "Objects/rows removed by the janitor",
      labelNames: ["kind"] as const,
      registers: [registry],
    }),
    activeJobs: new Gauge({
      name: "parallaxops_jobs_active",
      help: "Jobs currently in a non-terminal state",
      labelNames: ["state"] as const,
      registers: [registry],
    }),
  };
}
export type Metrics = ReturnType<typeof createMetrics>;
