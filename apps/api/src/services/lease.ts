import { eq, lt, or } from "drizzle-orm";
import type { Ctx } from "../context.ts";
import { schema } from "../db/client.ts";

const { maintenanceLeases: leases } = schema;

/**
 * Single-owner guard for periodic tasks (janitor, reconciler) when several API replicas run.
 * True when `holder` owns the lease `name` for the next `ttlMs`: it was free, expired, or already
 * ours (a renewal). One atomic upsert, so two replicas can never both win. The ttl must exceed
 * the task's period, or the holder loses the lease between its own ticks.
 */
export async function tryLease(
  ctx: Ctx,
  name: string,
  holder: string,
  ttlMs: number,
): Promise<boolean> {
  const now = ctx.now();
  const expiresAt = new Date(now.getTime() + ttlMs);
  const won = await ctx.db
    .insert(leases)
    .values({ name, holder, expiresAt })
    .onConflictDoUpdate({
      target: leases.name,
      set: { holder, expiresAt },
      setWhere: or(lt(leases.expiresAt, now), eq(leases.holder, holder)),
    })
    .returning({ name: leases.name });
  return won.length > 0;
}
