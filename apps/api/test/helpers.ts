import { createHash, randomUUID } from "node:crypto";
import { PGlite } from "@electric-sql/pglite";
import { keys } from "@parallaxops/contracts";
import { drizzle } from "drizzle-orm/pglite";
import { migrate } from "drizzle-orm/pglite/migrator";
import type { FastifyInstance } from "fastify";
import { buildApp } from "../src/app.ts";
import { loadConfig } from "../src/config.ts";
import type { Ctx } from "../src/context.ts";
import { type Db, MIGRATIONS_FOLDER, schema } from "../src/db/client.ts";
import { createMetrics } from "../src/metrics.ts";
import { MemoryStore } from "../src/services/storage.ts";
import { FakeStarter } from "../src/services/workflows.ts";

export interface Harness {
  app: FastifyInstance;
  ctx: Ctx;
  store: MemoryStore;
  starter: FakeStarter;
  clock: { now: Date };
  close(): Promise<void>;
}

export async function harness(): Promise<Harness> {
  const pg = new PGlite();
  const db = drizzle(pg, { schema });
  await migrate(db, { migrationsFolder: MIGRATIONS_FOLDER });
  const store = new MemoryStore();
  const starter = new FakeStarter();
  const clock = { now: new Date("2026-01-01T00:00:00Z") };
  const warnings: object[] = [];
  const ctx: Ctx = {
    db: db as unknown as Db,
    store,
    workflows: starter,
    metrics: createMetrics(),
    cfg: loadConfig({ NODE_ENV: "test", INTERNAL_API_SECRET: "test-secret-0123456789" }),
    now: () => clock.now,
    log: { info() {}, warn: (o) => warnings.push(o), error() {} },
  };
  const app = await buildApp(ctx);
  return { app, ctx, store, starter, clock, close: async () => (await app.close(), pg.close()) };
}

export const newTenant = () => `t-${randomUUID().replaceAll("-", "").slice(0, 16)}`;
export const SECRET = { "x-internal-secret": "test-secret-0123456789" };

export function api(h: Harness, tenant: string) {
  const headers = { "x-tenant-id": tenant };
  const call = async (
    method: "GET" | "POST" | "DELETE",
    url: string,
    payload?: unknown,
    extra = {},
  ) => {
    const res = await h.app.inject({
      method,
      url,
      headers: { ...headers, ...extra },
      payload: payload as never,
    });
    return {
      status: res.statusCode,
      body: res.body ? JSON.parse(res.body) : null,
      headers: res.headers,
    };
  };
  return {
    get: (url: string, extra = {}) => call("GET", url, undefined, extra),
    post: (url: string, body?: unknown, extra = {}) => call("POST", url, body ?? {}, extra),
    del: (url: string) => call("DELETE", url),
  };
}

export const sha = (b: Uint8Array) => createHash("sha256").update(b).digest("hex");
export const jpeg = (n: number, size = 64) => {
  const b = new Uint8Array(size).fill(n % 251);
  b.set([0xff, 0xd8, 0xff, 0xe0, n & 0xff]);
  return b;
};

/** Creates project + dataset, uploads `count` valid JPEGs through the real presign/complete flow. */
export async function seedDataset(h: Harness, tenant: string, count = 6) {
  const c = api(h, tenant);
  const project = (await c.post("/v1/projects", { name: "p" })).body;
  const dataset = (await c.post(`/v1/projects/${project.id}/datasets`, { name: "d" })).body;
  const files = Array.from({ length: count }, (_, i) => jpeg(i + 1));
  const req = await c.post(`/v1/datasets/${dataset.id}/uploads`, {
    files: files.map((f, i) => ({
      filename: `IMG_${i}.jpg`,
      contentType: "image/jpeg",
      sizeBytes: f.length,
      sha256: sha(f),
    })),
  });
  req.body.uploads.forEach((u: { imageId: string }, i: number) => {
    h.store.putBytes(keys.image(tenant, dataset.id, u.imageId, "jpg"), files[i] as Uint8Array);
  });
  const done = await c.post(`/v1/datasets/${dataset.id}/uploads/complete`, {
    imageIds: req.body.uploads.map((u: { imageId: string }) => u.imageId),
  });
  return {
    project,
    dataset,
    imageIds: req.body.uploads.map((u: { imageId: string }) => u.imageId),
    done,
  };
}

/** Drives a QUEUED job through every legal state to COMPLETED, as a worker would. */
export async function completeJob(h: Harness, jobId: string, payload: Record<string, unknown>) {
  const post = (e: unknown) =>
    h.app.inject({
      method: "POST",
      url: "/internal/v1/events",
      headers: SECRET,
      payload: e as never,
    });
  await post(event(jobId, "attempt_started", { workerId: "w-test" }));
  await post(event(jobId, "stage_started", { stage: "sparse_mapping", state: "RECONSTRUCTING" }));
  await post(event(jobId, "stage_started", { stage: "metrics", state: "EVALUATING" }));
  const r = await post(event(jobId, "completed", { state: "COMPLETED", payload }));
  return JSON.parse(r.body);
}

let seq = 0;
export const event = (jobId: string, type: string, extra: Record<string, unknown> = {}) => ({
  jobId,
  attemptNo: 1,
  seq: ++seq,
  type,
  payload: {},
  ...extra,
});
