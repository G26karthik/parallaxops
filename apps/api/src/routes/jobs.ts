import {
  ArtifactLinkSchema,
  CreateJobBody,
  ErrorSchema,
  JobCreatedResponse,
  JobSchema,
  LIMITS,
  SceneSchema,
} from "@parallaxops/contracts";
import { and, asc, eq } from "drizzle-orm";
import type { FastifyInstance } from "fastify";
import type { ZodTypeProvider } from "fastify-type-provider-zod";
import { z } from "zod";
import type { Ctx } from "../context.ts";
import { schema } from "../db/client.ts";
import { AppError } from "../errors.ts";
import {
  createJob,
  getJobRow,
  listJobs,
  requestCancel,
  serializeJob,
  startWorkflow,
} from "../services/jobs.ts";

const { artifacts, jobEvents } = schema;
const JobIdParam = z.object({ jobId: z.uuid() });
const IdemHeader = z.object({ "idempotency-key": z.string().min(8).max(128).optional() });

export function jobRoutes(app: FastifyInstance, ctx: Ctx) {
  const r = app.withTypeProvider<ZodTypeProvider>();
  const tag = { tags: ["jobs"] };

  r.post(
    "/v1/datasets/:datasetId/jobs",
    {
      schema: {
        ...tag,
        params: z.object({ datasetId: z.uuid() }),
        headers: IdemHeader,
        body: CreateJobBody,
        response: {
          200: JobCreatedResponse,
          202: JobCreatedResponse,
          422: ErrorSchema,
          429: ErrorSchema,
        },
      },
    },
    async (req, reply) => {
      const { job, replay } = await createJob(ctx, {
        tenantId: req.tenantId,
        datasetId: req.params.datasetId,
        idempotencyKey: req.headers["idempotency-key"],
        config: req.body.config,
        label: req.body.label,
        requestId: req.id,
      });
      if (replay) ctx.metrics.jobsCreated.inc({ replay: "true" });
      // Best effort: if the engine is down the job is still durable and the reconciler starts it.
      if (!job.workflowStartedAt && job.state === "QUEUED") await startWorkflow(ctx, job);
      return reply
        .status(replay ? 200 : 202)
        .header("location", `/v1/jobs/${job.id}`)
        .send({ id: job.id, state: job.state as never, idempotentReplay: replay });
    },
  );

  r.get(
    "/v1/jobs/:jobId",
    { schema: { ...tag, params: JobIdParam, response: { 200: JobSchema, 404: ErrorSchema } } },
    async (req) => serializeJob(ctx, await getJobRow(ctx, req.tenantId, req.params.jobId)),
  );

  r.get(
    "/v1/projects/:projectId/jobs",
    {
      schema: {
        ...tag,
        params: z.object({ projectId: z.uuid() }),
        response: { 200: z.array(JobSchema) },
      },
    },
    async (req) => {
      const rows = await listJobs(ctx, req.tenantId, req.params.projectId);
      return Promise.all(rows.map((row) => serializeJob(ctx, row)));
    },
  );

  r.post(
    "/v1/jobs/:jobId/cancel",
    {
      schema: {
        ...tag,
        params: JobIdParam,
        response: { 202: JobSchema, 404: ErrorSchema, 409: ErrorSchema },
      },
    },
    async (req, reply) => {
      const row = await requestCancel(ctx, req.tenantId, req.params.jobId, req.id);
      return reply.status(202).send(await serializeJob(ctx, row));
    },
  );

  r.get(
    "/v1/jobs/:jobId/events",
    {
      schema: {
        ...tag,
        params: JobIdParam,
        response: {
          200: z.array(
            z.object({
              seq: z.number(),
              attemptNo: z.number(),
              type: z.string(),
              stage: z.string().nullable(),
              fromState: z.string().nullable(),
              toState: z.string().nullable(),
              at: z.string(),
            }),
          ),
        },
      },
    },
    async (req) => {
      await getJobRow(ctx, req.tenantId, req.params.jobId);
      const rows = await ctx.db
        .select()
        .from(jobEvents)
        .where(and(eq(jobEvents.jobId, req.params.jobId), eq(jobEvents.tenantId, req.tenantId)))
        .orderBy(asc(jobEvents.id));
      return rows
        .filter((e) => e.type !== "heartbeat")
        .map((e) => ({
          seq: e.seq,
          attemptNo: e.attemptNo,
          type: e.type,
          stage: e.stage,
          fromState: e.fromState,
          toState: e.toState,
          at: e.createdAt.toISOString(),
        }));
    },
  );

  const links = async (tenantId: string, jobId: string, kinds?: string[]) => {
    await getJobRow(ctx, tenantId, jobId);
    const rows = await ctx.db
      .select()
      .from(artifacts)
      .where(and(eq(artifacts.jobId, jobId), eq(artifacts.tenantId, tenantId)));
    const expires = new Date(ctx.now().getTime() + LIMITS.presignTtlSeconds * 1000);
    const wanted = kinds ? rows.filter((a) => kinds.includes(a.kind)) : rows;
    return Promise.all(
      wanted.map(async (a) => {
        const filename = a.objectKey.split("/").pop() ?? a.kind;
        return {
          kind: a.kind,
          bytes: a.bytes,
          sha256: a.sha256,
          contentType: a.contentType,
          filename,
          url: await ctx.store.presignGet(a.objectKey, LIMITS.presignTtlSeconds, filename),
          expiresAt: expires.toISOString(),
        };
      }),
    );
  };

  r.get(
    "/v1/jobs/:jobId/artifacts",
    {
      schema: {
        ...tag,
        params: JobIdParam,
        response: { 200: z.array(ArtifactLinkSchema), 404: ErrorSchema },
      },
    },
    async (req) => links(req.tenantId, req.params.jobId),
  );

  r.get(
    "/v1/jobs/:jobId/scene",
    {
      schema: {
        ...tag,
        params: JobIdParam,
        response: { 200: SceneSchema, 404: ErrorSchema, 409: ErrorSchema },
      },
    },
    async (req) => {
      const job = await getJobRow(ctx, req.tenantId, req.params.jobId);
      if (job.state !== "COMPLETED")
        throw new AppError(
          409,
          "JOB_NOT_COMPLETED",
          "The 3D scene exists only for completed jobs.",
        );
      const got = await links(req.tenantId, req.params.jobId, ["points_preview", "cameras"]);
      const pts = got.find((a) => a.kind === "points_preview");
      const cams = got.find((a) => a.kind === "cameras");
      if (!pts || !cams)
        throw new AppError(404, "NOT_FOUND", "Scene artifacts are missing or expired.");
      const metrics = (job.metrics ?? {}) as { n_points3d?: number };
      return {
        jobId: job.id,
        pointCount: metrics.n_points3d ?? 0,
        pointsUrl: pts.url,
        camerasUrl: cams.url,
        expiresAt: pts.expiresAt,
        note:
          "Sparse SfM points (triangulated features), not a dense surface. Scale is arbitrary " +
          "unless an external source supplies it.",
      };
    },
  );
}
