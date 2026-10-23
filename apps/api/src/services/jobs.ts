import { createHash } from "node:crypto";
import {
  type ArtifactReport,
  canTransition,
  isTerminal,
  JOB_STATES,
  type Job,
  type JobState,
  keys,
  type ReconstructionConfig,
  ReconstructionConfigSchema,
  WORKFLOW_CONTRACT,
  type WorkerEvent,
} from "@parallaxops/contracts";
import { and, asc, count, desc, eq, inArray, isNull, lt, sql } from "drizzle-orm";
import type { Ctx } from "../context.ts";
import { schema } from "../db/client.ts";
import { AppError, conflict, notFound } from "../errors.ts";

const { jobs, jobAttempts, jobStages, jobEvents, artifacts, images, datasets, tenants, auditLog } =
  schema;

type JobRow = typeof jobs.$inferSelect;
const iso = (d: Date | null) => (d ? d.toISOString() : null);

const ACTIVE: JobState[] = ["PREPROCESSING", "RECONSTRUCTING", "EVALUATING"];
const PENDING_START: JobState[] = ["CREATED", "UPLOADING", "QUEUED"];
const MIN_IMAGES = 5;

export async function ensureTenant(ctx: Ctx, tenantId: string): Promise<void> {
  await ctx.db.insert(tenants).values({ id: tenantId }).onConflictDoNothing();
}

export async function audit(
  ctx: Ctx,
  tenantId: string,
  action: string,
  entity: string,
  entityId: string,
  requestId?: string,
  detail: Record<string, unknown> = {},
) {
  await ctx.db.insert(auditLog).values({ tenantId, action, entity, entityId, requestId, detail });
}

// ---------------------------------------------------------------------------------------------
// Serialization
// ---------------------------------------------------------------------------------------------

export async function serializeJob(ctx: Ctx, row: JobRow): Promise<Job> {
  const attempts = await ctx.db
    .select()
    .from(jobAttempts)
    .where(eq(jobAttempts.jobId, row.id))
    .orderBy(asc(jobAttempts.attemptNo));
  const latest = attempts.at(-1)?.attemptNo;
  const stages =
    latest === undefined
      ? []
      : await ctx.db
          .select()
          .from(jobStages)
          .where(and(eq(jobStages.jobId, row.id), eq(jobStages.attemptNo, latest)))
          .orderBy(asc(jobStages.startedAt));
  const arts = await ctx.db.select().from(artifacts).where(eq(artifacts.jobId, row.id));
  return {
    id: row.id,
    projectId: row.projectId,
    datasetId: row.datasetId,
    state: row.state as JobState,
    config: ReconstructionConfigSchema.parse(row.config),
    label: row.label,
    createdAt: row.createdAt.toISOString(),
    queuedAt: iso(row.queuedAt),
    startedAt: iso(row.startedAt),
    finishedAt: iso(row.finishedAt),
    queueWaitSeconds: row.queueWaitSeconds,
    durationSeconds: row.durationSeconds,
    quality: (row.quality as Job["quality"]) ?? null,
    metrics: row.metrics ?? null,
    groundTruth: row.groundTruth ?? null,
    timings: row.timings ?? null,
    resources: row.resources ?? null,
    warnings: (row.warnings ?? []) as Job["warnings"],
    diagnostic: (row.diagnostic as Job["diagnostic"]) ?? null,
    stages: stages.map((s) => ({
      name: s.name,
      state: s.state as "RUNNING" | "DONE" | "FAILED",
      startedAt: iso(s.startedAt),
      finishedAt: iso(s.finishedAt),
      seconds: s.seconds,
    })),
    attempts: attempts.map((a) => ({
      attemptNo: a.attemptNo,
      state: a.state as "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED",
      workerId: a.workerId,
      startedAt: iso(a.startedAt),
      finishedAt: iso(a.finishedAt),
      failure: (a.failure as Job["diagnostic"]) ?? null,
    })),
    artifacts: arts.map((a) => ({
      kind: a.kind,
      bytes: a.bytes,
      sha256: a.sha256,
      contentType: a.contentType,
    })),
  };
}

export async function getJobRow(ctx: Ctx, tenantId: string, jobId: string): Promise<JobRow> {
  const [row] = await ctx.db
    .select()
    .from(jobs)
    .where(and(eq(jobs.id, jobId), eq(jobs.tenantId, tenantId)));
  // Cross-tenant ids look exactly like missing ones: no existence oracle.
  if (!row) throw notFound("job");
  return row;
}

// ---------------------------------------------------------------------------------------------
// Creation (idempotent, quota-checked, durable before the workflow engine is touched)
// ---------------------------------------------------------------------------------------------

export interface CreateJobInput {
  tenantId: string;
  datasetId: string;
  idempotencyKey?: string;
  config?: Partial<ReconstructionConfig>;
  label?: string;
  requestId?: string;
}

function isUniqueViolation(e: unknown): boolean {
  const code =
    (e as { code?: string; cause?: { code?: string } }).code ??
    (e as { cause?: { code?: string } }).cause?.code;
  return code === "23505";
}

export async function createJob(
  ctx: Ctx,
  input: CreateJobInput,
): Promise<{ job: JobRow; replay: boolean }> {
  const { tenantId, datasetId } = input;
  const config = ReconstructionConfigSchema.parse(input.config ?? {});
  const requestHash = createHash("sha256")
    .update(JSON.stringify({ datasetId, config, label: input.label ?? null }))
    .digest("hex");

  if (input.idempotencyKey) {
    const [prior] = await ctx.db
      .select()
      .from(jobs)
      .where(and(eq(jobs.tenantId, tenantId), eq(jobs.idempotencyKey, input.idempotencyKey)));
    if (prior) return replayOrReject(prior, requestHash);
  }

  const [ds] = await ctx.db
    .select()
    .from(datasets)
    .where(and(eq(datasets.id, datasetId), eq(datasets.tenantId, tenantId)));
  if (!ds) throw notFound("dataset");

  const imgs = await ctx.db
    .select()
    .from(images)
    .where(and(eq(images.datasetId, datasetId), eq(images.status, "UPLOADED")))
    .orderBy(asc(images.filename), asc(images.id));
  if (imgs.length < MIN_IMAGES)
    throw new AppError(
      422,
      "TOO_FEW_IMAGES",
      `Dataset has ${imgs.length} completed uploads; at least ${MIN_IMAGES} are required.`,
      { advice: "Capture at least 15 overlapping photographs." },
    );

  const [tenant] = await ctx.db.select().from(tenants).where(eq(tenants.id, tenantId));
  const [{ pending = 0 } = {}] = await ctx.db
    .select({ pending: count() })
    .from(jobs)
    .where(and(eq(jobs.tenantId, tenantId), inArray(jobs.state, [...PENDING_START, ...ACTIVE])));
  const maxQueued = tenant?.maxQueuedJobs ?? 5;
  if (pending >= maxQueued)
    throw new AppError(
      429,
      "TENANT_QUOTA",
      `Tenant already has ${pending} queued or running jobs (limit ${maxQueued}).`,
      { limit: maxQueued },
      { "retry-after": "30" },
    );

  let inserted: JobRow | undefined;
  try {
    [inserted] = await ctx.db
      .insert(jobs)
      .values({
        tenantId,
        projectId: ds.projectId,
        datasetId,
        config,
        label: input.label ?? null,
        idempotencyKey: input.idempotencyKey ?? null,
        requestHash,
      })
      .returning();
  } catch (e) {
    if (input.idempotencyKey && isUniqueViolation(e)) {
      // Lost a race with a concurrent identical request: serve the winner.
      const [winner] = await ctx.db
        .select()
        .from(jobs)
        .where(and(eq(jobs.tenantId, tenantId), eq(jobs.idempotencyKey, input.idempotencyKey)));
      if (winner) return replayOrReject(winner, requestHash);
    }
    throw e;
  }
  if (!inserted) throw new Error("insert returned no row");
  const created = inserted;

  // Snapshot the inputs: later dataset edits cannot change what this job reconstructs.
  const manifestKey = keys.inputManifest(tenantId, datasetId, created.id);
  await ctx.store.put(
    manifestKey,
    JSON.stringify({
      contract: WORKFLOW_CONTRACT,
      tenantId,
      datasetId,
      jobId: created.id,
      outputPrefix: keys.jobRoot(tenantId, created.id),
      images: imgs.map((i) => ({
        imageId: i.id,
        filename: i.filename,
        key: i.objectKey,
        sha256: i.sha256,
        bytes: i.sizeBytes,
        contentType: i.contentType,
      })),
    }),
    "application/json",
  );
  const now = ctx.now();
  const [queued] = await ctx.db
    .update(jobs)
    .set({
      state: "QUEUED",
      queuedAt: now,
      inputManifestKey: manifestKey,
      workflowId: `reconstruct-${created.id}`,
    })
    .where(and(eq(jobs.id, created.id), eq(jobs.state, "CREATED")))
    .returning();
  if (!queued) throw new Error("job vanished during creation");
  ctx.metrics.jobsCreated.inc({ replay: "false" });
  ctx.metrics.transitions.inc({ to: "QUEUED" });
  await audit(ctx, tenantId, "job.create", "job", queued.id, input.requestId, {
    datasetId,
    images: imgs.length,
  });
  return { job: queued, replay: false };
}

function replayOrReject(prior: JobRow, requestHash: string) {
  if (prior.requestHash !== requestHash)
    throw new AppError(
      422,
      "IDEMPOTENCY_KEY_REUSED",
      "This Idempotency-Key was already used with a different request body.",
    );
  return { job: prior, replay: true };
}

/** Starts the workflow for a QUEUED job. Safe to call repeatedly: the workflow id is the job id. */
export async function startWorkflow(ctx: Ctx, row: JobRow): Promise<boolean> {
  if (!row.inputManifestKey || !row.workflowId) return false;
  try {
    await ctx.workflows.startReconstruction(row.workflowId, {
      contract: WORKFLOW_CONTRACT,
      jobId: row.id,
      tenantId: row.tenantId,
      datasetId: row.datasetId,
      inputManifestKey: row.inputManifestKey,
      config: ReconstructionConfigSchema.parse(row.config),
    });
  } catch (e) {
    ctx.metrics.workflowStartFailures.inc();
    ctx.log.warn({ err: String(e), jobId: row.id }, "workflow start failed; reconciler will retry");
    return false;
  }
  await ctx.db
    .update(jobs)
    .set({ workflowStartedAt: ctx.now() })
    .where(and(eq(jobs.id, row.id), isNull(jobs.workflowStartedAt)));
  return true;
}

/**
 * Outbox-style repair: any QUEUED job whose workflow never started (API crashed between commit and
 * start, or the engine was down) is started now. Idempotent by construction.
 */
export async function reconcileUnstarted(ctx: Ctx, olderThanMs = 3000): Promise<number> {
  const cutoff = new Date(ctx.now().getTime() - olderThanMs);
  const stuck = await ctx.db
    .select()
    .from(jobs)
    .where(and(eq(jobs.state, "QUEUED"), isNull(jobs.workflowStartedAt), lt(jobs.queuedAt, cutoff)))
    .orderBy(asc(jobs.queuedAt))
    .limit(20);
  let started = 0;
  for (const row of stuck) if (await startWorkflow(ctx, row)) started++;
  return started;
}

// ---------------------------------------------------------------------------------------------
// Cancellation
// ---------------------------------------------------------------------------------------------

export async function requestCancel(ctx: Ctx, tenantId: string, jobId: string, requestId?: string) {
  const row = await getJobRow(ctx, tenantId, jobId);
  const state = row.state as JobState;
  if (isTerminal(state)) throw conflict("JOB_FINISHED", `Job is already ${state}.`, { state });
  const now = ctx.now();
  await ctx.db.update(jobs).set({ cancelRequestedAt: now }).where(eq(jobs.id, jobId));
  if (row.workflowId) await ctx.workflows.cancel(row.workflowId);
  // Not yet picked up by a worker: nothing to tear down, finish immediately.
  if (PENDING_START.includes(state)) {
    await ctx.db
      .update(jobs)
      .set({ state: "CANCELLED", finishedAt: now })
      .where(and(eq(jobs.id, jobId), inArray(jobs.state, PENDING_START)));
    ctx.metrics.transitions.inc({ to: "CANCELLED" });
  }
  await audit(ctx, tenantId, "job.cancel", "job", jobId, requestId, { from: state });
  return getJobRow(ctx, tenantId, jobId);
}

// ---------------------------------------------------------------------------------------------
// Worker event ingestion
// ---------------------------------------------------------------------------------------------

export interface EventResult {
  applied: boolean;
  jobState: JobState;
  /** The worker must stop if the job was cancelled out from under it. */
  cancelRequested: boolean;
}

export async function applyEvent(ctx: Ctx, ev: WorkerEvent): Promise<EventResult> {
  return ctx.db.transaction(async (tx) => {
    const [row] = await tx.select().from(jobs).where(eq(jobs.id, ev.jobId)).for("update");
    if (!row) throw notFound("job");
    const current = row.state as JobState;
    const now = ctx.now();

    const ins = await tx
      .insert(jobEvents)
      .values({
        jobId: row.id,
        tenantId: row.tenantId,
        attemptNo: ev.attemptNo,
        seq: ev.seq,
        type: ev.type,
        stage: ev.stage ?? null,
        fromState: current,
        payload: ev.payload,
      })
      .onConflictDoNothing()
      .returning({ id: jobEvents.id });
    if (ins.length === 0) {
      // Replay of an event we already stored: acknowledge without touching state.
      return { applied: false, jobState: current, cancelRequested: row.cancelRequestedAt !== null };
    }

    const patch: Partial<typeof jobs.$inferInsert> = {};
    let target: JobState | undefined = ev.state;
    const p = ev.payload;

    // Result-bearing events are applied atomically or not at all: a `completed` that arrives while
    // the job is QUEUED or already terminal must not leak metrics or artifacts into the row.
    const terminalFor = {
      completed: "COMPLETED",
      failed: "FAILED",
      cancelled: "CANCELLED",
    } as const;
    const wanted = terminalFor[ev.type as keyof typeof terminalFor];
    if (wanted && !canTransition(current, wanted)) {
      ctx.metrics.rejectedTransitions.inc();
      ctx.log.warn(
        { jobId: row.id, from: current, to: wanted },
        "invalid terminal transition ignored",
      );
      await tx
        .update(jobEvents)
        .set({ toState: current })
        .where(eq(jobEvents.id, ins[0]?.id ?? -1));
      return { applied: true, jobState: current, cancelRequested: row.cancelRequestedAt !== null };
    }

    switch (ev.type) {
      case "attempt_started": {
        // A new attempt means every earlier one is over. An earlier attempt still RUNNING had its
        // worker lost (crash, kill, heartbeat timeout): record that instead of leaving it dangling.
        await tx
          .update(jobAttempts)
          .set({
            state: "FAILED",
            finishedAt: now,
            failure: {
              code: "ENGINE_CRASH",
              message:
                "Worker stopped reporting (crashed, killed or lost); the attempt was retried.",
              advice: "No action needed: the workflow engine retried the work automatically.",
              stage: "worker",
            },
          })
          .where(
            and(
              eq(jobAttempts.jobId, row.id),
              eq(jobAttempts.state, "RUNNING"),
              lt(jobAttempts.attemptNo, ev.attemptNo),
            ),
          );
        await tx
          .insert(jobAttempts)
          .values({
            jobId: row.id,
            tenantId: row.tenantId,
            attemptNo: ev.attemptNo,
            state: "RUNNING",
            workerId: ev.workerId ?? null,
            startedAt: now,
          })
          .onConflictDoNothing();
        if (!row.startedAt) {
          patch.startedAt = now;
          if (row.queuedAt) {
            const wait = (now.getTime() - row.queuedAt.getTime()) / 1000;
            patch.queueWaitSeconds = wait;
            ctx.metrics.queueWait.observe(wait);
          }
        }
        target ??= "PREPROCESSING";
        break;
      }
      case "stage_started": {
        await tx
          .insert(jobStages)
          .values({
            jobId: row.id,
            tenantId: row.tenantId,
            attemptNo: ev.attemptNo,
            name: ev.stage ?? "unknown",
            state: "RUNNING",
            startedAt: now,
          })
          .onConflictDoNothing();
        break;
      }
      case "stage_finished": {
        const seconds = typeof p.seconds === "number" ? p.seconds : null;
        await tx
          .insert(jobStages)
          .values({
            jobId: row.id,
            tenantId: row.tenantId,
            attemptNo: ev.attemptNo,
            name: ev.stage ?? "unknown",
            state: "DONE",
            startedAt: now,
            finishedAt: now,
            seconds,
          })
          .onConflictDoUpdate({
            target: [jobStages.jobId, jobStages.attemptNo, jobStages.name],
            set: { state: "DONE", finishedAt: now, seconds },
          });
        if (seconds !== null && ev.stage)
          ctx.metrics.stageDuration.observe({ stage: ev.stage }, seconds);
        break;
      }
      case "heartbeat":
        patch.lastHeartbeatAt = now;
        break;
      case "attempt_failed": {
        await tx
          .update(jobAttempts)
          .set({
            state: "FAILED",
            finishedAt: now,
            failure: (p.diagnostic as Record<string, unknown>) ?? null,
          })
          .where(and(eq(jobAttempts.jobId, row.id), eq(jobAttempts.attemptNo, ev.attemptNo)));
        if (p.willRetry === true) target = "QUEUED";
        break;
      }
      case "completed": {
        // Artifact keys must live under this job's own tenant prefix. A buggy or compromised worker
        // can therefore never attach another tenant's objects to this job.
        const prefix = keys.jobRoot(row.tenantId, row.id);
        const arts = ((p.artifacts as ArtifactReport[] | undefined) ?? []).filter(
          (a) => a.key.startsWith(prefix) && !a.key.includes(".."),
        );
        for (const a of arts) {
          await tx
            .insert(artifacts)
            .values({
              jobId: row.id,
              tenantId: row.tenantId,
              attemptNo: ev.attemptNo,
              kind: a.kind,
              objectKey: a.key,
              bytes: a.bytes,
              sha256: a.sha256,
              contentType: a.contentType,
            })
            .onConflictDoNothing();
          ctx.metrics.artifactBytes.observe({ kind: a.kind }, a.bytes);
        }
        Object.assign(patch, {
          quality: (p.quality as Record<string, unknown>) ?? null,
          metrics: (p.metrics as Record<string, unknown>) ?? null,
          groundTruth: (p.groundTruth as Record<string, unknown>) ?? null,
          timings: (p.timings as Record<string, number>) ?? null,
          resources: (p.resources as Record<string, unknown>) ?? null,
          warnings: (p.warnings as Record<string, unknown>[]) ?? [],
          artifactsPublishedAt: now,
        });
        await tx
          .update(jobAttempts)
          .set({ state: "SUCCEEDED", finishedAt: now })
          .where(and(eq(jobAttempts.jobId, row.id), eq(jobAttempts.attemptNo, ev.attemptNo)));
        target = "COMPLETED";
        break;
      }
      case "failed": {
        Object.assign(patch, {
          diagnostic: (p.diagnostic as Record<string, unknown>) ?? null,
          metrics: (p.metrics as Record<string, unknown>) ?? null,
          timings: (p.timings as Record<string, number>) ?? null,
          warnings: (p.warnings as Record<string, unknown>[]) ?? [],
        });
        await tx
          .update(jobAttempts)
          .set({
            state: "FAILED",
            finishedAt: now,
            failure: (p.diagnostic as Record<string, unknown>) ?? null,
          })
          // Workflow-originated terminal events carry a synthetic attempt number; close whatever
          // attempt is still running instead of matching on it.
          .where(and(eq(jobAttempts.jobId, row.id), eq(jobAttempts.state, "RUNNING")));
        target = "FAILED";
        break;
      }
      case "cancelled": {
        await tx
          .update(jobAttempts)
          .set({ state: "CANCELLED", finishedAt: now })
          .where(and(eq(jobAttempts.jobId, row.id), eq(jobAttempts.state, "RUNNING")));
        target = "CANCELLED";
        break;
      }
    }

    let next = current;
    if (target && target !== current) {
      if (canTransition(current, target)) {
        next = target;
        patch.state = target;
        ctx.metrics.transitions.inc({ to: target });
        if (isTerminal(target)) {
          patch.finishedAt = now;
          const start = row.startedAt ?? patch.startedAt ?? row.queuedAt ?? row.createdAt;
          const dur = (now.getTime() - start.getTime()) / 1000;
          patch.durationSeconds = dur;
          ctx.metrics.jobDuration.observe({ outcome: target.toLowerCase() }, dur);
        }
      } else {
        // e.g. a late event after CANCELLED. Recorded in the log, never applied.
        ctx.metrics.rejectedTransitions.inc();
        ctx.log.warn(
          { jobId: row.id, from: current, to: target, type: ev.type },
          "invalid transition ignored",
        );
      }
    }
    // Terminal states are immutable: drop result patches that would rewrite a finished job.
    if (isTerminal(current))
      for (const k of Object.keys(patch)) delete (patch as Record<string, unknown>)[k];

    if (Object.keys(patch).length > 0) await tx.update(jobs).set(patch).where(eq(jobs.id, row.id));
    await tx
      .update(jobEvents)
      .set({ toState: next })
      .where(eq(jobEvents.id, ins[0]?.id ?? -1));
    return { applied: true, jobState: next, cancelRequested: row.cancelRequestedAt !== null };
  });
}

// ---------------------------------------------------------------------------------------------
// Listing helpers
// ---------------------------------------------------------------------------------------------

export async function listJobs(ctx: Ctx, tenantId: string, projectId?: string, limit = 50) {
  return ctx.db
    .select()
    .from(jobs)
    .where(and(eq(jobs.tenantId, tenantId), projectId ? eq(jobs.projectId, projectId) : undefined))
    .orderBy(desc(jobs.createdAt))
    .limit(limit);
}

export async function refreshActiveGauge(ctx: Ctx) {
  const rows = await ctx.db
    .select({ state: jobs.state, n: sql<number>`count(*)::int` })
    .from(jobs)
    .groupBy(jobs.state);
  for (const s of JOB_STATES) ctx.metrics.activeJobs.set({ state: s }, 0);
  for (const r of rows) ctx.metrics.activeJobs.set({ state: r.state }, r.n);
}
