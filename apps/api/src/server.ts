import { buildApp } from "./app.ts";
import { loadConfig } from "./config.ts";
import type { Ctx } from "./context.ts";
import { connectPg, migratePostgres } from "./db/client.ts";
import { createMetrics } from "./metrics.ts";
import { runCleanup } from "./services/cleanup.ts";
import { reconcileUnstarted } from "./services/jobs.ts";
import { S3Store } from "./services/storage.ts";
import { TemporalStarter } from "./services/workflows.ts";

async function main() {
  const cfg = loadConfig();
  await migratePostgres(cfg.DATABASE_URL);
  const handle = connectPg(cfg.DATABASE_URL);
  const store = new S3Store(cfg);
  await store.ensureBucket();
  const workflows = await TemporalStarter.connect(cfg);

  // Fastify owns the logger; the same instance is shared with services after the app is built.
  const ctx: Ctx = {
    db: handle.db,
    store,
    workflows,
    metrics: createMetrics(),
    cfg,
    now: () => new Date(),
    log: { info: () => {}, warn: () => {}, error: () => {} },
  };
  const app = await buildApp(ctx, { logger: true });
  ctx.log = {
    info: (o, m) => app.log.info(o, m),
    warn: (o, m) => app.log.warn(o, m),
    error: (o, m) => app.log.error(o, m),
  };

  const timers = [
    setInterval(
      () =>
        void reconcileUnstarted(ctx).catch((e) =>
          app.log.error({ err: String(e) }, "reconcile failed"),
        ),
      10_000,
    ),
    setInterval(
      () => void runCleanup(ctx).catch((e) => app.log.error({ err: String(e) }, "cleanup failed")),
      10 * 60_000,
    ),
  ];

  const shutdown = async (sig: string) => {
    app.log.info({ sig }, "shutting down");
    for (const t of timers) clearInterval(t);
    await app.close();
    await handle.close();
    process.exit(0);
  };
  process.on("SIGINT", () => void shutdown("SIGINT"));
  process.on("SIGTERM", () => void shutdown("SIGTERM"));

  await app.listen({ port: cfg.PORT, host: cfg.HOST });
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
