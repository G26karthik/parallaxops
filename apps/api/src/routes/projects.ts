import { randomUUID } from "node:crypto";
import {
  CompleteUploadsBody,
  CompleteUploadsResponse,
  CreateDatasetBody,
  CreateProjectBody,
  DatasetSchema,
  ErrorSchema,
  EXTENSION_BY_TYPE,
  ImageSchema,
  keys,
  LIMITS,
  MAGIC,
  PreflightSchema,
  ProjectSchema,
  RequestUploadsBody,
  RequestUploadsResponse,
  sanitizeFilename,
  UploadFileSchema,
  UploadTargetSchema,
  WORKFLOW_CONTRACT,
} from "@parallaxops/contracts";
import { and, asc, count, desc, eq, inArray, ne, sql, sum } from "drizzle-orm";
import type { FastifyInstance } from "fastify";
import type { ZodTypeProvider } from "fastify-type-provider-zod";
import { z } from "zod";
import type { Ctx } from "../context.ts";
import { schema } from "../db/client.ts";
import { AppError, conflict, notFound } from "../errors.ts";
import { audit } from "../services/jobs.ts";
import { afterCursor, cutPage, newestFirst, PageQuery } from "../services/paging.ts";
import { checksumHeader } from "../services/storage.ts";

/** Page size when the caller names none: large enough that the web app shows everything it has. */
const DEFAULT_PAGE = 200;
const { projects, datasets, images, jobs, tenants } = schema;
const IdParam = <K extends string>(name: K) =>
  z.object({ [name]: z.uuid() } as Record<K, z.ZodUUID>);
const iso = (d: Date) => d.toISOString();

type DatasetRow = typeof datasets.$inferSelect;
type ImageRow = typeof images.$inferSelect;

export async function datasetView(ctx: Ctx, d: DatasetRow) {
  const [agg] = await ctx.db
    .select({ n: count(), bytes: sum(images.sizeBytes) })
    .from(images)
    .where(and(eq(images.datasetId, d.id), eq(images.status, "UPLOADED")));
  return {
    id: d.id,
    projectId: d.projectId,
    name: d.name,
    imageCount: agg?.n ?? 0,
    totalBytes: Number(agg?.bytes ?? 0),
    preflight: {
      status: d.preflightStatus as "NONE" | "RUNNING" | "DONE" | "FAILED",
      report: d.preflightReport ?? null,
      error: (d.preflightError as z.infer<typeof PreflightSchema>["error"]) ?? null,
      updatedAt: d.preflightUpdatedAt ? iso(d.preflightUpdatedAt) : null,
    },
    createdAt: iso(d.createdAt),
  };
}

const imageView = (i: ImageRow) => ({
  id: i.id,
  filename: i.filename,
  contentType: i.contentType as "image/jpeg" | "image/png",
  sizeBytes: i.sizeBytes,
  sha256: i.sha256,
  status: i.status as "PENDING" | "UPLOADED" | "REJECTED",
  rejectReason: i.rejectReason,
  createdAt: iso(i.createdAt),
});

async function getDataset(ctx: Ctx, tenantId: string, id: string) {
  const [d] = await ctx.db
    .select()
    .from(datasets)
    .where(and(eq(datasets.id, id), eq(datasets.tenantId, tenantId)));
  if (!d) throw notFound("dataset");
  return d;
}

export function projectRoutes(app: FastifyInstance, ctx: Ctx) {
  const r = app.withTypeProvider<ZodTypeProvider>();
  const tag = { tags: ["projects"] };

  r.post(
    "/v1/projects",
    { schema: { ...tag, body: CreateProjectBody, response: { 201: ProjectSchema } } },
    async (req, reply) => {
      const [p] = await ctx.db
        .insert(projects)
        .values({
          tenantId: req.tenantId,
          name: req.body.name,
          description: req.body.description ?? null,
        })
        .returning();
      if (!p) throw new Error("insert failed");
      await audit(ctx, req.tenantId, "project.create", "project", p.id, req.id);
      return reply
        .status(201)
        .send({ id: p.id, name: p.name, description: p.description, createdAt: iso(p.createdAt) });
    },
  );

  r.get(
    "/v1/projects",
    { schema: { ...tag, querystring: PageQuery, response: { 200: z.array(ProjectSchema) } } },
    async (req, reply) => {
      const limit = req.query.limit ?? DEFAULT_PAGE;
      const rows = await ctx.db
        .select()
        .from(projects)
        .where(
          and(
            eq(projects.tenantId, req.tenantId),
            afterCursor(projects.createdAt, projects.id, req.query.cursor),
          ),
        )
        .orderBy(...newestFirst(projects.createdAt, projects.id))
        .limit(limit + 1);
      const { items, next } = cutPage(rows, limit);
      if (next) reply.header("x-next-cursor", next);
      return items.map((p) => ({
        id: p.id,
        name: p.name,
        description: p.description,
        createdAt: iso(p.createdAt),
      }));
    },
  );

  r.get(
    "/v1/projects/:projectId",
    {
      schema: {
        ...tag,
        params: IdParam("projectId"),
        response: { 200: ProjectSchema, 404: ErrorSchema },
      },
    },
    async (req) => {
      const [p] = await ctx.db
        .select()
        .from(projects)
        .where(and(eq(projects.id, req.params.projectId), eq(projects.tenantId, req.tenantId)));
      if (!p) throw notFound("project");
      return { id: p.id, name: p.name, description: p.description, createdAt: iso(p.createdAt) };
    },
  );

  // ---------------- datasets ----------------
  r.post(
    "/v1/projects/:projectId/datasets",
    {
      schema: {
        tags: ["datasets"],
        params: IdParam("projectId"),
        body: CreateDatasetBody,
        response: { 201: DatasetSchema },
      },
    },
    async (req, reply) => {
      const [p] = await ctx.db
        .select({ id: projects.id })
        .from(projects)
        .where(and(eq(projects.id, req.params.projectId), eq(projects.tenantId, req.tenantId)));
      if (!p) throw notFound("project");
      const [d] = await ctx.db
        .insert(datasets)
        .values({ tenantId: req.tenantId, projectId: p.id, name: req.body.name })
        .returning();
      if (!d) throw new Error("insert failed");
      await audit(ctx, req.tenantId, "dataset.create", "dataset", d.id, req.id);
      return reply.status(201).send(await datasetView(ctx, d));
    },
  );

  r.get(
    "/v1/projects/:projectId/datasets",
    {
      schema: {
        tags: ["datasets"],
        params: IdParam("projectId"),
        querystring: PageQuery,
        response: { 200: z.array(DatasetSchema) },
      },
    },
    async (req, reply) => {
      const limit = req.query.limit ?? DEFAULT_PAGE;
      const rows = await ctx.db
        .select()
        .from(datasets)
        .where(
          and(
            eq(datasets.projectId, req.params.projectId),
            eq(datasets.tenantId, req.tenantId),
            afterCursor(datasets.createdAt, datasets.id, req.query.cursor),
          ),
        )
        .orderBy(...newestFirst(datasets.createdAt, datasets.id))
        .limit(limit + 1);
      const { items, next } = cutPage(rows, limit);
      if (next) reply.header("x-next-cursor", next);
      return Promise.all(items.map((d) => datasetView(ctx, d)));
    },
  );

  r.get(
    "/v1/datasets/:datasetId",
    {
      schema: {
        tags: ["datasets"],
        params: IdParam("datasetId"),
        response: { 200: DatasetSchema, 404: ErrorSchema },
      },
    },
    async (req) => datasetView(ctx, await getDataset(ctx, req.tenantId, req.params.datasetId)),
  );

  r.get(
    "/v1/datasets/:datasetId/images",
    {
      schema: {
        tags: ["datasets"],
        params: IdParam("datasetId"),
        response: { 200: z.array(ImageSchema) },
      },
    },
    async (req) => {
      const d = await getDataset(ctx, req.tenantId, req.params.datasetId);
      const rows = await ctx.db
        .select()
        .from(images)
        .where(and(eq(images.datasetId, d.id), ne(images.status, "REJECTED")))
        .orderBy(asc(images.filename), asc(images.id));
      return rows.map(imageView);
    },
  );

  r.delete(
    "/v1/datasets/:datasetId/images/:imageId",
    {
      schema: {
        tags: ["datasets"],
        params: z.object({ datasetId: z.uuid(), imageId: z.uuid() }),
        response: { 204: z.null(), 409: ErrorSchema },
      },
    },
    async (req, reply) => {
      const d = await getDataset(ctx, req.tenantId, req.params.datasetId);
      const [img] = await ctx.db
        .select()
        .from(images)
        .where(and(eq(images.id, req.params.imageId), eq(images.datasetId, d.id)));
      if (!img) throw notFound("image");
      const [busy] = await ctx.db
        .select({ n: count() })
        .from(jobs)
        .where(
          and(
            eq(jobs.datasetId, d.id),
            inArray(jobs.state, [
              "CREATED",
              "UPLOADING",
              "QUEUED",
              "PREPROCESSING",
              "RECONSTRUCTING",
              "EVALUATING",
            ]),
          ),
        );
      if ((busy?.n ?? 0) > 0)
        throw conflict("DATASET_IN_USE", "A job is using this dataset; wait for it to finish.");
      await ctx.store.delete(img.objectKey);
      await ctx.db.delete(images).where(eq(images.id, img.id));
      await audit(ctx, req.tenantId, "image.delete", "image", img.id, req.id);
      return reply.status(204).send(null);
    },
  );

  // ---------------- uploads (presigned PUT direct to object storage) ----------------
  r.post(
    "/v1/datasets/:datasetId/uploads",
    {
      schema: {
        tags: ["uploads"],
        params: IdParam("datasetId"),
        body: RequestUploadsBody,
        response: { 201: RequestUploadsResponse, 413: ErrorSchema, 429: ErrorSchema },
      },
    },
    async (req, reply) => {
      const d = await getDataset(ctx, req.tenantId, req.params.datasetId);

      // Plan and insert under a per-dataset lock: the caps, the dedup check and the inserts cannot
      // interleave with another request for the same dataset (two tabs, a double click).
      const plan = await ctx.db.transaction(async (tx) => {
        await tx.execute(sql`select pg_advisory_xact_lock(hashtext(${d.id}))`);
        const live = await tx
          .select()
          .from(images)
          .where(and(eq(images.datasetId, d.id), ne(images.status, "REJECTED")));
        const bySha = new Map(live.map((i) => [i.sha256, i.id]));
        const fresh = req.body.files.filter(
          (f, idx, all) =>
            !bySha.has(f.sha256) && all.findIndex((x) => x.sha256 === f.sha256) === idx,
        );
        if (live.length + fresh.length > LIMITS.maxFilesPerDataset)
          throw new AppError(
            413,
            "TOO_MANY_FILES",
            `A dataset holds at most ${LIMITS.maxFilesPerDataset} images.`,
          );
        const addBytes = fresh.reduce((a, f) => a + f.sizeBytes, 0);
        if (live.reduce((a, i) => a + i.sizeBytes, 0) + addBytes > LIMITS.maxDatasetBytes)
          throw new AppError(413, "DATASET_TOO_LARGE", "Dataset byte limit exceeded.");
        const [t] = await tx.select().from(tenants).where(eq(tenants.id, req.tenantId));
        const [used] = await tx
          .select({ b: sum(images.sizeBytes) })
          .from(images)
          .where(and(eq(images.tenantId, req.tenantId), ne(images.status, "REJECTED")));
        if (Number(used?.b ?? 0) + addBytes > (t?.maxStorageBytes ?? 5e9))
          throw new AppError(429, "TENANT_STORAGE_QUOTA", "Tenant storage quota exceeded.");

        // Sequential on purpose: a repeat later in the same batch must see the earlier insert.
        const out: {
          f: z.infer<typeof UploadFileSchema>;
          filename: string;
          imageId: string;
          upload: boolean;
          key: string;
        }[] = [];
        for (const f of req.body.files) {
          const filename = sanitizeFilename(f.filename);
          const existing = bySha.get(f.sha256);
          if (existing) {
            out.push({ f, filename, imageId: existing, upload: false, key: "" });
            continue;
          }
          const imageId = randomUUID();
          const key = keys.image(req.tenantId, d.id, imageId, EXTENSION_BY_TYPE[f.contentType]);
          await tx.insert(images).values({
            id: imageId,
            tenantId: req.tenantId,
            datasetId: d.id,
            filename,
            contentType: f.contentType,
            sizeBytes: f.sizeBytes,
            sha256: f.sha256,
            objectKey: key,
            createdAt: ctx.now(),
          });
          bySha.set(f.sha256, imageId);
          out.push({ f, filename, imageId, upload: true, key });
        }
        return out;
      });

      const expires = new Date(ctx.now().getTime() + LIMITS.presignTtlSeconds * 1000);
      const uploads = await Promise.all(
        plan.map(
          async (p): Promise<z.infer<typeof UploadTargetSchema>> =>
            p.upload
              ? {
                  imageId: p.imageId,
                  filename: p.filename,
                  sha256: p.f.sha256,
                  action: "UPLOAD" as const,
                  url: await ctx.store.presignPut(
                    p.key,
                    p.f.contentType,
                    p.f.sizeBytes,
                    p.f.sha256,
                    LIMITS.presignTtlSeconds,
                  ),
                  headers: {
                    "content-type": p.f.contentType,
                    "x-amz-checksum-sha256": checksumHeader(p.f.sha256),
                  },
                  expiresAt: iso(expires),
                }
              : {
                  imageId: p.imageId,
                  filename: p.filename,
                  sha256: p.f.sha256,
                  action: "DUPLICATE" as const,
                  url: null,
                  headers: {},
                  expiresAt: null,
                },
        ),
      );
      return reply.status(201).send({ uploads });
    },
  );

  r.post(
    "/v1/datasets/:datasetId/uploads/complete",
    {
      schema: {
        tags: ["uploads"],
        params: IdParam("datasetId"),
        body: CompleteUploadsBody,
        response: { 200: CompleteUploadsResponse },
      },
    },
    async (req) => {
      const d = await getDataset(ctx, req.tenantId, req.params.datasetId);
      const rows = await ctx.db
        .select()
        .from(images)
        .where(and(eq(images.datasetId, d.id), inArray(images.id, req.body.imageIds)));
      const out: ImageRow[] = [];
      for (const img of rows) {
        if (img.status !== "PENDING") {
          out.push(img);
          continue;
        }
        const reject = async (reason: string) => {
          await ctx.store.delete(img.objectKey).catch(() => undefined);
          ctx.metrics.uploadRejected.inc({ reason: reason.split(":")[0] ?? "other" });
          const [u] = await ctx.db
            .update(images)
            .set({ status: "REJECTED", rejectReason: reason })
            .where(eq(images.id, img.id))
            .returning();
          out.push(u ?? img);
        };
        const head = await ctx.store.head(img.objectKey);
        if (!head) {
          out.push({ ...img, rejectReason: "object not uploaded yet" });
          continue;
        }
        if (head.bytes !== img.sizeBytes)
          await reject(`size_mismatch: declared ${img.sizeBytes} B, stored ${head.bytes} B`);
        else if (head.bytes > LIMITS.maxImageBytes) await reject("too_large");
        else {
          const magic = MAGIC[img.contentType as keyof typeof MAGIC];
          const prefix = await ctx.store.readPrefix(img.objectKey, magic.length);
          if (!magic.every((b, i) => prefix[i] === b))
            await reject(`bad_magic: content is not ${img.contentType}`);
          // The store checked the signed digest while receiving the bytes; only a store that kept
          // none (or an object from before this was signed) is hashed again by streaming it.
          else if (
            ((await ctx.store.storedSha256(img.objectKey)) ??
              (await ctx.store.sha256(img.objectKey))) !== img.sha256
          )
            await reject("checksum_mismatch: uploaded bytes differ from the declared SHA-256");
          else {
            const [u] = await ctx.db
              .update(images)
              .set({ status: "UPLOADED", rejectReason: null })
              .where(eq(images.id, img.id))
              .returning();
            out.push(u ?? img);
          }
        }
      }
      return { images: out.map(imageView) };
    },
  );

  // ---------------- preflight (QC runs in the worker; this only schedules it) ----------------
  r.post(
    "/v1/datasets/:datasetId/preflight",
    {
      schema: {
        tags: ["datasets"],
        params: IdParam("datasetId"),
        response: { 202: DatasetSchema, 422: ErrorSchema, 503: ErrorSchema },
      },
    },
    async (req, reply) => {
      const d = await getDataset(ctx, req.tenantId, req.params.datasetId);
      const imgs = await ctx.db
        .select()
        .from(images)
        .where(and(eq(images.datasetId, d.id), eq(images.status, "UPLOADED")))
        .orderBy(asc(images.filename), asc(images.id));
      if (imgs.length === 0)
        throw new AppError(422, "NO_IMAGES", "Upload at least one image before running preflight.");
      const ref = `preflight-${Date.now()}`;
      const manifestKey = keys.inputManifest(req.tenantId, d.id, ref);
      await ctx.store.put(
        manifestKey,
        JSON.stringify({
          contract: WORKFLOW_CONTRACT,
          tenantId: req.tenantId,
          datasetId: d.id,
          jobId: null,
          outputPrefix: "",
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
      const [updated] = await ctx.db
        .update(datasets)
        .set({
          preflightStatus: "RUNNING",
          preflightError: null,
          preflightUpdatedAt: ctx.now(),
        })
        .where(eq(datasets.id, d.id))
        .returning();
      try {
        await ctx.workflows.startPreflight(`${ref}-${d.id}`, {
          contract: WORKFLOW_CONTRACT,
          tenantId: req.tenantId,
          datasetId: d.id,
          inputManifestKey: manifestKey,
        });
      } catch (e) {
        ctx.log.warn({ err: String(e) }, "preflight workflow start failed");
        await ctx.db
          .update(datasets)
          .set({ preflightStatus: "FAILED", preflightUpdatedAt: ctx.now() })
          .where(eq(datasets.id, d.id));
        throw new AppError(
          503,
          "WORKFLOW_ENGINE_UNAVAILABLE",
          "The workflow engine is not reachable.",
        );
      }
      return reply.status(202).send(await datasetView(ctx, updated ?? d));
    },
  );
}
