import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { api, type Harness, harness, jpeg, newTenant, seedDataset, sha } from "./helpers.ts";

let h: Harness;
beforeAll(async () => {
  h = await harness();
});
afterAll(() => h.close());

describe("limits hold under concurrent requests", () => {
  it("never queues more jobs than the tenant limit when requests arrive together", async () => {
    const t = newTenant();
    const { dataset } = await seedDataset(h, t);
    const c = api(h, t);
    const burst = await Promise.all(
      Array.from({ length: 12 }, () => c.post(`/v1/datasets/${dataset.id}/jobs`, {})),
    );
    const accepted = burst.filter((r) => r.status === 202).length;
    const refused = burst.filter((r) => r.status === 429).length;
    expect(accepted).toBe(5); // the default queue limit
    expect(refused).toBe(7);
  });

  it("answers DUPLICATE, not 500, when two requests upload the same file at once", async () => {
    const t = newTenant();
    const c = api(h, t);
    const p = (await c.post("/v1/projects", { name: "p" })).body;
    const d = (await c.post(`/v1/projects/${p.id}/datasets`, { name: "d" })).body;
    const b = jpeg(77);
    const body = {
      files: [
        { filename: "a.jpg", contentType: "image/jpeg", sizeBytes: b.length, sha256: sha(b) },
      ],
    };
    const [r1, r2] = await Promise.all([
      c.post(`/v1/datasets/${d.id}/uploads`, body),
      c.post(`/v1/datasets/${d.id}/uploads`, body),
    ]);
    expect([r1.status, r2.status]).toEqual([201, 201]);
    const actions = [r1, r2].map((r) => r.body.uploads[0].action).sort();
    expect(actions).toEqual(["DUPLICATE", "UPLOAD"]);
    expect(r1.body.uploads[0].imageId).toBe(r2.body.uploads[0].imageId);
  });

  it("enforces the per-dataset file cap across simultaneous requests", async () => {
    const t = newTenant();
    const c = api(h, t);
    const p = (await c.post("/v1/projects", { name: "p" })).body;
    const d = (await c.post(`/v1/projects/${p.id}/datasets`, { name: "d" })).body;
    const batch = (offset: number) => ({
      files: Array.from({ length: 50 }, (_, i) => {
        const b = jpeg(offset + i, 70);
        return {
          filename: `${offset + i}.jpg`,
          contentType: "image/jpeg",
          sizeBytes: b.length,
          sha256: sha(b),
        };
      }),
    });
    const [a, b] = await Promise.all([
      c.post(`/v1/datasets/${d.id}/uploads`, batch(10)),
      c.post(`/v1/datasets/${d.id}/uploads`, batch(100)),
    ]);
    expect([a.status, b.status].sort()).toEqual([201, 413]); // 100 files would exceed the cap of 80
  });
});

describe("a storage failure while creating a job leaves nothing behind", () => {
  it("does not consume quota and the next attempt succeeds", async () => {
    const t = newTenant();
    const { dataset } = await seedDataset(h, t);
    const c = api(h, t);
    const realPut = h.store.put.bind(h.store);
    let failures = 6; // more than the queue limit: old code would lock the tenant out
    h.store.put = async (...a: Parameters<typeof realPut>) => {
      if (a[0].includes("/manifests/") && failures-- > 0) throw new Error("storage unavailable");
      return realPut(...a);
    };
    try {
      for (let i = 0; i < 6; i++)
        expect((await c.post(`/v1/datasets/${dataset.id}/jobs`, {})).status).toBe(500);
    } finally {
      h.store.put = realPut;
    }
    const ok = await c.post(`/v1/datasets/${dataset.id}/jobs`, {});
    expect(ok.status).toBe(202); // the six failures did not count as queued jobs
    expect(
      (
        await c.get(
          `/v1/projects/${(await c.get(`/v1/datasets/${dataset.id}`)).body.projectId}/jobs`,
        )
      ).body,
    ).toHaveLength(1);
  });
});
