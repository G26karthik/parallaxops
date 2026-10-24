import { randomUUID } from "node:crypto";
import { buildApp } from "./app.ts";
import { loadConfig } from "./config.ts";
import type { Ctx } from "./context.ts";
import { connectPg, ensureDatabase, migratePostgres } from "./db/client.ts";
import { createMetrics } from "./metrics.ts";
import { runCleanup } from "./services/cleanup.ts";
import { reconcileUnstarted } from "./services/jobs.ts";
import { tryLease } from "./services/lease.ts";
import { S3Store } from "./services/storage.ts";
import { TemporalStarter } from "./services/workflows.ts";

async function main() {
  const cfg = loadConfig();
  if (cfg.NODE_ENV !== "production") await ensureDatabase(cfg.DATABASE_URL);
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

  // Every replica runs these timers, but only the one holding the lease does the work. The lease
  // outlives the period, so the holder keeps it between ticks; if it dies, another takes over.
  const me = randomUUID();
  const every = (name: string, periodMs: number, task: () => Promise<unknown>) =>
    setInterval(() => {
      void tryLease(ctx, name, me, periodMs * 2.5)
        .then((mine) => mine && task())
        .catch((e) => app.log.error({ err: String(e) }, `${name} failed`));
    }, periodMs);
  const timers = [
    every("reconcile", 10_000, () => reconcileUnstarted(ctx)),
    every("cleanup", 10 * 60_000, () => runCleanup(ctx)),
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
