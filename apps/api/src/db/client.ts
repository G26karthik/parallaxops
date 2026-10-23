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

/** Dev convenience: create the target database on first boot (the portable server ships no psql). */
export async function ensureDatabase(url: string): Promise<void> {
  const u = new URL(url);
  const name = decodeURIComponent(u.pathname.slice(1));
  if (!/^[A-Za-z0-9_]+$/.test(name)) throw new Error("unsafe database name");
  u.pathname = "/postgres";
  const admin = new pg.Client({ connectionString: u.toString() });
  await admin.connect();
  try {
    const r = await admin.query("select 1 from pg_database where datname = $1", [name]);
    if (r.rowCount === 0) await admin.query(`create database "${name}"`);
  } finally {
    await admin.end();
  }
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
