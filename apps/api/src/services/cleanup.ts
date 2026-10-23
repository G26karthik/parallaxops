import { TERMINAL_STATES } from "@parallaxops/contracts";
import { and, eq, inArray, isNotNull, lt } from "drizzle-orm";
import type { Ctx } from "../context.ts";
import { schema } from "../db/client.ts";

const { images, jobs, artifacts } = schema;

export interface CleanupReport {
  abandonedUploads: number;
  expiredArtifactJobs: number;
  expiredArtifactObjects: number;
}

/**
 * Retention janitor. Removes (1) uploads that were presigned but never completed and (2) artifacts
 * of long-finished jobs. Job rows and metrics stay, so history and comparisons survive; only blobs go.
 * Every removal is counted in Prometheus so cleanup is observable.
 */
export async function runCleanup(ctx: Ctx): Promise<CleanupReport> {
  const now = ctx.now();
  const report: CleanupReport = {
    abandonedUploads: 0,
    expiredArtifactJobs: 0,
    expiredArtifactObjects: 0,
  };

  const uploadCutoff = new Date(now.getTime() - ctx.cfg.PENDING_UPLOAD_TTL_MINUTES * 60_000);
  const stale = await ctx.db
    .select()
    .from(images)
    .where(and(eq(images.status, "PENDING"), lt(images.createdAt, uploadCutoff)));
  for (const img of stale) {
    await ctx.store.delete(img.objectKey).catch(() => undefined);
    await ctx.db.delete(images).where(eq(images.id, img.id));
    report.abandonedUploads++;
    ctx.metrics.cleanupRemoved.inc({ kind: "abandoned_upload" });
  }

  const retentionCutoff = new Date(now.getTime() - ctx.cfg.ARTIFACT_RETENTION_DAYS * 86_400_000);
  const old = await ctx.db
    .select()
    .from(jobs)
    .where(
      and(
        inArray(jobs.state, [...TERMINAL_STATES]),
        isNotNull(jobs.artifactsPublishedAt),
        lt(jobs.finishedAt, retentionCutoff),
      ),
    );
  for (const job of old) {
    const arts = await ctx.db.select().from(artifacts).where(eq(artifacts.jobId, job.id));
    for (const a of arts) {
      await ctx.store.delete(a.objectKey).catch(() => undefined);
      report.expiredArtifactObjects++;
      ctx.metrics.cleanupRemoved.inc({ kind: "expired_artifact" });
    }
    await ctx.db.delete(artifacts).where(eq(artifacts.jobId, job.id));
    await ctx.db.update(jobs).set({ artifactsPublishedAt: null }).where(eq(jobs.id, job.id));
    report.expiredArtifactJobs++;
  }
  return report;
}
