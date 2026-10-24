import { afterAll, beforeAll, expect, it } from "vitest";
import { tryLease } from "../src/services/lease.ts";
import { type Harness, harness } from "./helpers.ts";

let h: Harness;
beforeAll(async () => {
  h = await harness();
});
afterAll(() => h.close());

it("lets one replica run a periodic task at a time and hands over when its lease expires", async () => {
  expect(await tryLease(h.ctx, "janitor", "replica-a", 30_000)).toBe(true);
  expect(await tryLease(h.ctx, "janitor", "replica-b", 30_000)).toBe(false);
  expect(await tryLease(h.ctx, "janitor", "replica-a", 30_000)).toBe(true); // the holder renews

  h.clock.now = new Date(h.clock.now.getTime() + 31_000); // replica-a stopped renewing
  expect(await tryLease(h.ctx, "janitor", "replica-b", 30_000)).toBe(true);
  expect(await tryLease(h.ctx, "janitor", "replica-a", 30_000)).toBe(false);
});

it("keeps leases for different tasks apart", async () => {
  expect(await tryLease(h.ctx, "reconciler", "replica-a", 30_000)).toBe(true);
  expect(await tryLease(h.ctx, "cleanup", "replica-b", 30_000)).toBe(true);
});
