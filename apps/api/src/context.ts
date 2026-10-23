import type { Config } from "./config.ts";
import type { Db } from "./db/client.ts";
import type { Metrics } from "./metrics.ts";
import type { ObjectStore } from "./services/storage.ts";
import type { WorkflowStarter } from "./services/workflows.ts";

export interface Logger {
  info(obj: object, msg?: string): void;
  warn(obj: object, msg?: string): void;
  error(obj: object, msg?: string): void;
}

/** Everything a service needs. Injected so tests swap in PGlite, MemoryStore and FakeStarter. */
export interface Ctx {
  db: Db;
  store: ObjectStore;
  workflows: WorkflowStarter;
  metrics: Metrics;
  cfg: Config;
  log: Logger;
  now: () => Date;
}
