/** Writes the generated OpenAPI document. `pnpm openapi` then derives typed client types from it. */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { buildApp } from "./app.ts";
import { loadConfig } from "./config.ts";
import type { Ctx } from "./context.ts";
import { createMetrics } from "./metrics.ts";
import { MemoryStore } from "./services/storage.ts";
import { FakeStarter } from "./services/workflows.ts";

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, "../../../docs/openapi.json");

const cfg = loadConfig({ ...process.env, NODE_ENV: "test" });
// The spec only needs route schemas; no database or network is touched.
const ctx = {
  db: undefined as never,
  store: new MemoryStore(),
  workflows: new FakeStarter(),
  metrics: createMetrics(),
  cfg,
  now: () => new Date(),
  log: { info() {}, warn() {}, error() {} },
} satisfies Ctx;
const app = await buildApp(ctx);
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, `${JSON.stringify(app.swagger(), null, 2)}\n`);
await app.close();
console.log(`wrote ${out}`);
