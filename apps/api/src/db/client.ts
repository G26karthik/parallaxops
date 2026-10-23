import { fileURLToPath } from "node:url";
import { drizzle as drizzlePg } from "drizzle-orm/node-postgres";
import { migrate as migratePg } from "drizzle-orm/node-postgres/migrator";
import type { PgDatabase, PgQueryResultHKT } from "drizzle-orm/pg-core";
import pg from "pg";
import * as schema from "./schema.ts";

/** Driver-agnostic handle: node-postgres in production, PGlite (in-process Postgres) in tests. */
export type Db = PgDatabase<PgQueryResultHKT, typeof schema>;
export { schema };

export const MIGRATIONS_FOLDER = fileURLToPath(new URL("../../drizzle", import.meta.url));

export interface DbHandle {
  db: Db;
  close(): Promise<void>;
  pool?: pg.Pool;
}

export function connectPg(url: string, max = 10): DbHandle {
  const pool = new pg.Pool({ connectionString: url, max, idleTimeoutMillis: 30_000 });
  return { db: drizzlePg(pool, { schema }), close: () => pool.end(), pool };
}

export async function migratePostgres(url: string): Promise<void> {
  const h = connectPg(url, 1);
  try {
    await migratePg(h.db as unknown as Parameters<typeof migratePg>[0], {
      migrationsFolder: MIGRATIONS_FOLDER,
    });
  } finally {
    await h.close();
  }
}
