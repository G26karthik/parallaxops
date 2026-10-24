import { readFileSync } from "node:fs";
import { WorkerEventSchema } from "@parallaxops/contracts";
import { afterAll, beforeAll, expect, it } from "vitest";
import { api, type Harness, harness, newTenant, SECRET, seedDataset } from "./helpers.ts";

// What the real worker sends, recorded by services/vision-worker/tests/test_event_contract.py.
// Replaying it here means a change on either side of the contract fails a test on both.
const stream: Record<string, unknown>[] = JSON.parse(
  readFileSync(
    new URL("../../../packages/contracts/fixtures/worker_events.json", import.meta.url),
    "utf8",
  ),
);

let h: Harness;
beforeAll(async () => {
  h = await harness();
});
afterAll(() => h.close());

it("accepts every event the worker sends and walks the job through its phases", async () => {
  const t = newTenant();
  const { dataset } = await seedDataset(h, t);
  const c = api(h, t);
  const job = (await c.post(`/v1/datasets/${dataset.id}/jobs`, {})).body;

  const seen: string[] = [];
  for (const e of stream) {
    const body = { ...e, jobId: job.id };
    expect(WorkerEventSchema.safeParse(body).success, JSON.stringify(e)).toBe(true);
    const res = await h.app.inject({
      method: "POST",
      url: "/internal/v1/events",
      headers: SECRET,
      payload: body as never,
    });
    expect(res.statusCode, res.body).toBe(200);
    const state = (await c.get(`/v1/jobs/${job.id}`)).body.state as string;
    if (seen.at(-1) !== state) seen.push(state);
  }
  expect(seen).toEqual(["PREPROCESSING", "RECONSTRUCTING", "EVALUATING"]);

  const stages = (await c.get(`/v1/jobs/${job.id}`)).body.stages as {
    name: string;
    state: string;
  }[];
  expect(stages.map((s) => s.name).sort()).toEqual([
    "download_inputs",
    "feature_extraction",
    "metrics",
    "publish_artifacts",
    "qc",
  ]);
  expect(stages.every((s) => s.state === "DONE")).toBe(true);

  // Phases were really entered, so the workflow's own `completed` is accepted.
  const done = await h.app.inject({
    method: "POST",
    url: "/internal/v1/events",
    headers: SECRET,
    payload: {
      jobId: job.id,
      attemptNo: 1,
      seq: 10_000,
      type: "completed",
      state: "COMPLETED",
      payload: { metrics: { n_points3d: 1 } },
    } as never,
  });
  expect(JSON.parse(done.body).jobState).toBe("COMPLETED");
});
