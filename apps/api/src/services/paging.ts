import { type AnyColumn, desc, type SQL, sql } from "drizzle-orm";
import { z } from "zod";
import { badRequest } from "../errors.ts";

/** Query parameters of every list endpoint. `cursor` is the `x-next-cursor` of the page before. */
export const PageQuery = z.object({
  limit: z.coerce.number().int().min(1).max(200).optional(),
  cursor: z.string().max(200).optional(),
});
export type PageQuery = z.infer<typeof PageQuery>;

const Cursor = z.tuple([z.iso.datetime(), z.uuid()]);

export const encodeCursor = (row: { createdAt: Date; id: string }) =>
  Buffer.from(JSON.stringify([row.createdAt.toISOString(), row.id])).toString("base64url");

function decodeCursor(cursor: string) {
  try {
    return Cursor.parse(JSON.parse(Buffer.from(cursor, "base64url").toString("utf8")));
  } catch {
    throw badRequest("INVALID_CURSOR", "The cursor is not one this API returned.");
  }
}

// Timestamps are compared at millisecond precision because that is all a JS Date (and so a cursor)
// can hold; ordering by the full microsecond value would let rows slip between two pages.
const ms = (createdAt: AnyColumn) => sql`date_trunc('milliseconds', ${createdAt})`;

/** Newest first, ties broken by id so the order is total and stable between requests. */
export const newestFirst = (createdAt: AnyColumn, id: AnyColumn) => [desc(ms(createdAt)), desc(id)];

/** The rows strictly after `cursor` in `newestFirst` order (undefined: the first page). */
export function afterCursor(
  createdAt: AnyColumn,
  id: AnyColumn,
  cursor: string | undefined,
): SQL | undefined {
  if (!cursor) return undefined;
  const [at, rowId] = decodeCursor(cursor);
  return sql`(${ms(createdAt)}, ${id}) < (${at}::timestamptz, ${rowId}::uuid)`;
}

/** Callers fetch `limit + 1` rows; the extra one only says whether another page exists. */
export function cutPage<T extends { createdAt: Date; id: string }>(rows: T[], limit: number) {
  const items = rows.slice(0, limit);
  const last = items.at(-1);
  return { items, next: rows.length > limit && last ? encodeCursor(last) : null };
}
