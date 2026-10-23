import { PreflightResultSchema, WorkerEventSchema } from "@parallaxops/contracts";
import { and, eq, sql } from "drizzle-orm";
import type { FastifyInstance } from "fastify";
import type { ZodTypeProvider } from "fastify-type-provider-zod";
import { z } from "zod";
import type { Ctx } from "../context.ts";
import { schema } from "../db/client.ts";
import { notFound } from "../errors.ts";
import { requireInternalSecret } from "../plugins.ts";
import { runCleanup } from "../services/cleanup.ts";
import { applyEvent, reconcileUnstarted, refreshActiveGauge } from "../services/jobs.ts";

const { datasets } = schema;

async function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  let t: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([
      p,
      new Promise<never>((_, rej) => {
        t = setTimeout(() => rej(new Error("timeout")), ms);
      }),
    ]);
  } finally {
    clearTimeout(t);
  }
}

export function systemRoutes(app: FastifyInstance, ctx: Ctx) {
  const r = app.withTypeProvider<ZodTypeProvider>();

  r.get("/healthz", { schema: { hide: true } }, async () => ({ status: "ok" }));

  r.get("/readyz", { schema: { hide: true } }, async (_req, reply) => {
    const check = async (fn: () => Promise<unknown>) => {
      try {
        await withTimeout(fn(), 2000);
        return "ok";
      } catch (e) {
        return `down: ${String((e as Error).message)}`;
      }
    };
    const components = {
      database: await check(() => ctx.db.execute(sql`select 1`)),
      objectStore: await check(() => ctx.store.ping()),
      workflowEngine: await check(() => ctx.workflows.ping()),
    };
    const ok = Object.values(components).every((v) => v === "ok");
    return reply.status(ok ? 200 : 503).send({ status: ok ? "ready" : "degraded", components });
  });

  r.get("/metrics", { schema: { hide: true } }, async (_req, reply) => {
    await refreshActiveGauge(ctx);
    return reply.type(ctx.metrics.registry.contentType).send(await ctx.metrics.registry.metrics());
  });

  // ---- worker -> API (shared secret; never reachable from the browser's tenant header) ----
  r.post(
    "/internal/v1/events",
    {
      schema: {
        hide: true,
        body: WorkerEventSchema,
        response: {
          200: z.object({
            applied: z.boolean(),
            jobState: z.string(),
            cancelRequested: z.boolean(),
          }),
        },
      },
    },
    async (req) => {
      requireInternalSecret(ctx, req);
      return applyEvent(ctx, req.body);
    },
  );

  r.post(
    "/internal/v1/preflight",
    { schema: { hide: true, body: PreflightResultSchema } },
    async (req) => {
      requireInternalSecret(ctx, req);
      const b = req.body;
      const [d] = await ctx.db.select().from(datasets).where(eq(datasets.id, b.datasetId));
      if (!d) throw notFound("dataset");
      await ctx.db
        .update(datasets)
        .set({
          preflightStatus: b.ok ? "DONE" : "FAILED",
          preflightReport: b.ok ? (b.report ?? {}) : null,
          preflightError: b.ok ? null : (b.error ?? null),
          preflightUpdatedAt: ctx.now(),
        })
        .where(and(eq(datasets.id, d.id), eq(datasets.tenantId, d.tenantId)));
      return { ok: true };
    },
  );

  r.post("/internal/v1/cleanup", { schema: { hide: true } }, async (req) => {
    requireInternalSecret(ctx, req);
    return runCleanup(ctx);
  });

  r.post("/internal/v1/reconcile", { schema: { hide: true } }, async (req) => {
    requireInternalSecret(ctx, req);
    return { started: await reconcileUnstarted(ctx, 0) };
  });
}
