import { randomUUID } from "node:crypto";
import { eq } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { schema } from "../src/db/client.ts";
import { runCleanup } from "../src/services/cleanup.ts";
import { reconcileUnstarted } from "../src/services/jobs.ts";
import {
  api,
  completeJob,
  event,
  type Harness,
  harness,
  jpeg,
  newTenant,
  SECRET,
  seedDataset,
} from "./helpers.ts";

let h: Harness;
beforeAll(async () => {
  h = await harness();
});
afterAll(() => h.close());

const send = (body: unknown, secret: Record<string, string> = SECRET) =>
  h.app.inject({
    method: "POST",
    url: "/internal/v1/events",
    headers: secret,
    payload: body as never,
  });
const ev = async (jobId: string, type: string, extra: Record<string, unknown> = {}) => {
  const r = await send(event(jobId, type, extra));
  return JSON.parse(r.body);
};

describe("job creation", () => {
  it("returns 202 immediately and queues exactly one workflow", async () => {
    const t = newTenant();
    const { dataset } = await seedDataset(h, t);
    const r = await api(h, t).post(`/v1/datasets/${dataset.id}/jobs`, { label: "baseline" });
    expect(r.status).toBe(202);
    expect(r.body).toMatchObject({ state: "QUEUED", idempotentReplay: false });
    expect(r.headers.location).toBe(`/v1/jobs/${r.body.id}`);
    expect(h.starter.started.filter((s) => s.id === `reconstruct-${r.body.id}`)).toHaveLength(1);
    // the input manifest was snapshotted to object storage, tenant-prefixed
    const manifestKeys = [...h.store.objects.keys()].filter((k) =>
      k.includes(`manifests/${r.body.id}`),
    );
    expect(manifestKeys).toHaveLength(1);
    expect(manifestKeys[0]).toContain(`tenants/${t}/`);
  });

  it("is idempotent per tenant: same key + body replays, changed body is rejected", async () => {
    const t = newTenant();
    const { dataset } = await seedDataset(h, t);
    const c = api(h, t);
    const key = { "idempotency-key": `key-${randomUUID()}` };
    const first = await c.post(`/v1/datasets/${dataset.id}/jobs`, { label: "a" }, key);
    const second = await c.post(`/v1/datasets/${dataset.id}/jobs`, { label: "a" }, key);
    expect(second.status).toBe(200);
    expect(second.body).toMatchObject({ id: first.body.id, idempotentReplay: true });
    const clash = await c.post(`/v1/datasets/${dataset.id}/jobs`, { label: "different" }, key);
    expect(clash.status).toBe(422);
    expect(clash.body.error.code).toBe("IDEMPOTENCY_KEY_REUSED");
    // concurrent identical requests converge on one job
    const k2 = { "idempotency-key": `key-${randomUUID()}` };
    const burst = await Promise.all(
      Array.from({ length: 5 }, () => c.post(`/v1/datasets/${dataset.id}/jobs`, {}, k2)),
    );
    expect(new Set(burst.map((r) => r.body.id)).size).toBe(1);
    expect(
      h.starter.started.filter((s) => s.id === `reconstruct-${burst[0]?.body.id}`),
    ).toHaveLength(1);
  });

  it("scopes idempotency keys per tenant", async () => {
    const a = newTenant();
    const b = newTenant();
    const da = (await seedDataset(h, a)).dataset;
    const db = (await seedDataset(h, b)).dataset;
    const key = { "idempotency-key": `shared-${randomUUID()}` };
    const ja = await api(h, a).post(`/v1/datasets/${da.id}/jobs`, {}, key);
    const jb = await api(h, b).post(`/v1/datasets/${db.id}/jobs`, {}, key);
    expect(ja.body.id).not.toBe(jb.body.id);
    expect(jb.body.idempotentReplay).toBe(false);
  });

  it("rejects datasets with too few completed images using actionable advice", async () => {
    const t = newTenant();
    const { dataset } = await seedDataset(h, t, 3);
    const r = await api(h, t).post(`/v1/datasets/${dataset.id}/jobs`, {});
    expect(r.status).toBe(422);
    expect(r.body.error.code).toBe("TOO_FEW_IMAGES");
    expect(r.body.error.details.advice).toMatch(/15/);
  });

  it("validates config bounds", async () => {
    const t = newTenant();
    const { dataset } = await seedDataset(h, t);
    const r = await api(h, t).post(`/v1/datasets/${dataset.id}/jobs`, {
      config: { maxImageSize: 999999 },
    });
    expect(r.status).toBe(400);
  });

  it("applies a per-tenant queue quota with Retry-After", async () => {
    const t = newTenant();
    const { dataset } = await seedDataset(h, t);
    const c = api(h, t);
    for (let i = 0; i < 5; i++)
      expect((await c.post(`/v1/datasets/${dataset.id}/jobs`, {})).status).toBe(202);
    const over = await c.post(`/v1/datasets/${dataset.id}/jobs`, {});
    expect(over.status).toBe(429);
    expect(over.body.error.code).toBe("TENANT_QUOTA");
    expect(over.headers["retry-after"]).toBe("30");
  });

  it("stays durable when the workflow engine is down and the reconciler starts it later", async () => {
    const t = newTenant();
    const { dataset } = await seedDataset(h, t);
    h.starter.failNext = 1;
    const r = await api(h, t).post(`/v1/datasets/${dataset.id}/jobs`, {});
    expect(r.status).toBe(202); // accepted even though the engine call failed
    expect(h.starter.started.some((s) => s.id === `reconstruct-${r.body.id}`)).toBe(false);
    h.clock.now = new Date(h.clock.now.getTime() + 10_000);
    expect(await reconcileUnstarted(h.ctx)).toBeGreaterThanOrEqual(1);
    expect(h.starter.started.filter((s) => s.id === `reconstruct-${r.body.id}`)).toHaveLength(1);
    expect(await reconcileUnstarted(h.ctx)).toBe(0); // idempotent: nothing left to start
  });
});

describe("state machine via worker events", () => {
  async function queued() {
    const t = newTenant();
    const { dataset } = await seedDataset(h, t);
    const job = (await api(h, t).post(`/v1/datasets/${dataset.id}/jobs`, {})).body;
    return { t, c: api(h, t), id: job.id as string };
  }

  it("walks QUEUED -> PREPROCESSING -> RECONSTRUCTING -> EVALUATING -> COMPLETED", async () => {
    const { t, c, id } = await queued();
    h.clock.now = new Date(h.clock.now.getTime() + 5000);
    await ev(id, "attempt_started", { workerId: "w1" });
    expect((await c.get(`/v1/jobs/${id}`)).body.state).toBe("PREPROCESSING");
    expect((await c.get(`/v1/jobs/${id}`)).body.queueWaitSeconds).toBeGreaterThanOrEqual(5);
    await ev(id, "stage_started", { stage: "feature_extraction", state: "RECONSTRUCTING" });
    await ev(id, "stage_finished", { stage: "feature_extraction", payload: { seconds: 7.5 } });
    await ev(id, "stage_started", { stage: "metrics", state: "EVALUATING" });
    const done = await ev(id, "completed", {
      state: "COMPLETED",
      payload: {
        metrics: { n_points3d: 5432, n_registered: 23 },
        quality: { level: "good", reasons: ["all rubric thresholds met"] },
        timings: { total: 70.1 },
        artifacts: [
          {
            kind: "report",
            key: `tenants/${t}/jobs/${id}/attempt-1/report.json`,
            bytes: 5,
            sha256: "c".repeat(64),
            contentType: "application/json",
          },
        ],
      },
    });
    expect(done.jobState).toBe("COMPLETED");
    const job = (await c.get(`/v1/jobs/${id}`)).body;
    expect(job.metrics.n_points3d).toBe(5432);
    expect(job.stages.find((s: { name: string }) => s.name === "feature_extraction").seconds).toBe(
      7.5,
    );
    expect(job.attempts[0]).toMatchObject({ attemptNo: 1, state: "SUCCEEDED", workerId: "w1" });
    expect(job.artifacts).toHaveLength(1);
    expect(job.durationSeconds).not.toBeNull();
  });

  it("ignores replayed events (same job/attempt/seq)", async () => {
    const { id } = await queued();
    const e = event(id, "attempt_started", { workerId: "w1" });
    expect(JSON.parse((await send(e)).body).applied).toBe(true);
    expect(JSON.parse((await send(e)).body).applied).toBe(false);
  });

  it("refuses invalid transitions and keeps terminal states immutable", async () => {
    const { c, id } = await queued();
    // QUEUED -> COMPLETED is not allowed
    const bad = await ev(id, "completed", { state: "COMPLETED", payload: { metrics: { x: 1 } } });
    expect(bad.jobState).toBe("QUEUED");
    expect((await c.get(`/v1/jobs/${id}`)).body.metrics).toBeNull();
    await ev(id, "attempt_started");
    await ev(id, "failed", {
      state: "FAILED",
      payload: { diagnostic: { code: "NO_FEATURES", message: "m", advice: "a" } },
    });
    const after = await ev(id, "completed", { state: "COMPLETED", payload: { metrics: { x: 2 } } });
    expect(after.jobState).toBe("FAILED");
    const job = (await c.get(`/v1/jobs/${id}`)).body;
    expect(job.state).toBe("FAILED");
    expect(job.diagnostic.code).toBe("NO_FEATURES");
    expect(job.metrics).toBeNull();
  });

  it("marks an attempt whose worker vanished as failed when the next attempt starts", async () => {
    const { c, id } = await queued();
    await ev(id, "attempt_started", { workerId: "w-dead" });
    await ev(id, "stage_started", { stage: "feature_matching", state: "RECONSTRUCTING" });
    // worker is killed here: no attempt_failed is ever sent. Temporal retries on a new worker.
    await ev(id, "attempt_started", { attemptNo: 2, workerId: "w-alive" });
    const job = (await c.get(`/v1/jobs/${id}`)).body;
    expect(
      job.attempts.map((a: { attemptNo: number; state: string }) => [a.attemptNo, a.state]),
    ).toEqual([
      [1, "FAILED"],
      [2, "RUNNING"],
    ]);
    expect(job.attempts[0].failure.message).toMatch(/stopped reporting/);
  });

  it("ignores artifacts whose keys are outside the job's own tenant prefix", async () => {
    const { t, c, id } = await queued();
    const done = await completeJob(h, id, {
      metrics: { n_points3d: 1 },
      artifacts: [
        {
          kind: "report",
          key: `tenants/${t}/jobs/${id}/attempt-1/report.json`,
          bytes: 1,
          sha256: "a".repeat(64),
          contentType: "application/json",
        },
        {
          kind: "stolen",
          key: "tenants/another-tenant-xyz/jobs/other/attempt-1/secret.bin",
          bytes: 1,
          sha256: "b".repeat(64),
          contentType: "application/json",
        },
        {
          kind: "traversal",
          key: `tenants/${t}/jobs/${id}/../../other/x`,
          bytes: 1,
          sha256: "c".repeat(64),
          contentType: "application/json",
        },
      ],
    });
    expect(done.jobState).toBe("COMPLETED");
    const kinds = (await c.get(`/v1/jobs/${id}`)).body.artifacts.map(
      (a: { kind: string }) => a.kind,
    );
    expect(kinds).toEqual(["report"]);
  });

  it("records retries as separate immutable attempts and re-queues", async () => {
    const { c, id } = await queued();
    await ev(id, "attempt_started", { workerId: "w1" });
    await ev(id, "attempt_failed", {
      payload: { diagnostic: { code: "ENGINE_CRASH", message: "x", advice: "y" }, willRetry: true },
    });
    expect((await c.get(`/v1/jobs/${id}`)).body.state).toBe("QUEUED");
    await ev(id, "attempt_started", { attemptNo: 2, workerId: "w2" });
    const job = (await c.get(`/v1/jobs/${id}`)).body;
    expect(
      job.attempts.map((a: { attemptNo: number; state: string }) => [a.attemptNo, a.state]),
    ).toEqual([
      [1, "FAILED"],
      [2, "RUNNING"],
    ]);
    expect(job.state).toBe("PREPROCESSING");
  });
});

describe("cancellation", () => {
  it("cancels a queued job immediately and propagates to the workflow engine", async () => {
    const t = newTenant();
    const { dataset } = await seedDataset(h, t);
    const c = api(h, t);
    const job = (await c.post(`/v1/datasets/${dataset.id}/jobs`, {})).body;
    const r = await c.post(`/v1/jobs/${job.id}/cancel`);
    expect(r.status).toBe(202);
    expect(r.body.state).toBe("CANCELLED");
    expect(h.starter.cancelled).toContain(`reconstruct-${job.id}`);
    expect((await c.post(`/v1/jobs/${job.id}/cancel`)).status).toBe(409);
  });

  it("flags a running job for cancellation and tells the worker via the event reply", async () => {
    const t = newTenant();
    const { dataset } = await seedDataset(h, t);
    const c = api(h, t);
    const job = (await c.post(`/v1/datasets/${dataset.id}/jobs`, {})).body;
    await ev(job.id, "attempt_started");
    const r = await c.post(`/v1/jobs/${job.id}/cancel`);
    expect(r.body.state).toBe("PREPROCESSING"); // worker must confirm the teardown
    const hb = await ev(job.id, "heartbeat");
    expect(hb.cancelRequested).toBe(true);
    // Heartbeats only stamp the job row: storing one per 5 s would grow job_events without bound.
    const stored = await h.ctx.db
      .select()
      .from(schema.jobEvents)
      .where(eq(schema.jobEvents.jobId, job.id));
    expect(stored.map((e) => e.type)).not.toContain("heartbeat");
    const [row] = await h.ctx.db.select().from(schema.jobs).where(eq(schema.jobs.id, job.id));
    expect(row?.lastHeartbeatAt).not.toBeNull();
    // The workflow reports with a synthetic attempt number; the running attempt must still close.
    const done = await ev(job.id, "cancelled", { state: "CANCELLED", attemptNo: 99 });
    expect(done.jobState).toBe("CANCELLED");
    const after = (await c.get(`/v1/jobs/${job.id}`)).body;
    expect(after.attempts.map((a: { state: string }) => a.state)).toEqual(["CANCELLED"]);
  });
});

describe("cleanup", () => {
  it("removes abandoned uploads and expired artifacts, and is observable", async () => {
    const t = newTenant();
    const c = api(h, t);
    const p = (await c.post("/v1/projects", { name: "p" })).body;
    const d = (await c.post(`/v1/projects/${p.id}/datasets`, { name: "d" })).body;
    const r = await c.post(`/v1/datasets/${d.id}/uploads`, {
      files: [
        { filename: "a.jpg", contentType: "image/jpeg", sizeBytes: 64, sha256: "d".repeat(64) },
      ],
    });
    const key = `tenants/${t}/datasets/${d.id}/images/${r.body.uploads[0].imageId}.jpg`;
    h.store.putBytes(key, jpeg(1));
    h.clock.now = new Date(h.clock.now.getTime() + 2 * 3600_000);
    const rep = await runCleanup(h.ctx);
    expect(rep.abandonedUploads).toBeGreaterThanOrEqual(1);
    expect(h.store.objects.has(key)).toBe(false);

    const { dataset } = await seedDataset(h, t);
    const job = (await c.post(`/v1/datasets/${dataset.id}/jobs`, {})).body;
    const art = `tenants/${t}/jobs/${job.id}/attempt-1/report.json`;
    h.store.putBytes(art, new Uint8Array([1]), "application/json");
    await completeJob(h, job.id, {
      artifacts: [
        {
          kind: "report",
          key: art,
          bytes: 1,
          sha256: "e".repeat(64),
          contentType: "application/json",
        },
      ],
    });
    h.clock.now = new Date(h.clock.now.getTime() + 31 * 86_400_000);
    const rep2 = await runCleanup(h.ctx);
    expect(rep2.expiredArtifactJobs).toBeGreaterThanOrEqual(1);
    expect(h.store.objects.has(art)).toBe(false);
    expect((await c.get(`/v1/jobs/${job.id}`)).body.state).toBe("COMPLETED"); // history survives
    const metrics = await h.ctx.metrics.registry.getSingleMetricAsString(
      "parallaxops_cleanup_removed_total",
    );
    expect(metrics).toContain("expired_artifact");
  });
});
