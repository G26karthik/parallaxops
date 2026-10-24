import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { api, type Harness, harness, newTenant, seedDataset } from "./helpers.ts";

let h: Harness;
beforeAll(async () => {
  h = await harness();
});
afterAll(() => h.close());

type Client = ReturnType<typeof api>;

/** Follows `x-next-cursor` until the list ends; returns the ids in the order they came. */
async function walk(c: Client, url: string, limit: number) {
  const ids: string[] = [];
  let cursor: string | undefined;
  let pages = 0;
  do {
    const sep = url.includes("?") ? "&" : "?";
    const r = await c.get(`${url}${sep}limit=${limit}${cursor ? `&cursor=${cursor}` : ""}`);
    expect(r.status).toBe(200);
    expect(r.body.length).toBeLessThanOrEqual(limit);
    ids.push(...r.body.map((x: { id: string }) => x.id));
    cursor = r.headers["x-next-cursor"] as string | undefined;
    pages++;
  } while (cursor);
  return { ids, pages };
}

describe("cursor paging", () => {
  it("pages projects newest first with no gaps and no repeats", async () => {
    const c = api(h, newTenant());
    for (let i = 0; i < 5; i++) await c.post("/v1/projects", { name: `p${i}` });
    const all = (await c.get("/v1/projects")).body.map((p: { id: string }) => p.id);
    expect(all).toHaveLength(5);
    const { ids, pages } = await walk(c, "/v1/projects", 2);
    expect(ids).toEqual(all);
    expect(pages).toBe(3);
  });

  it("has no cursor when everything fits in one page", async () => {
    const c = api(h, newTenant());
    await c.post("/v1/projects", { name: "only" });
    const r = await c.get("/v1/projects?limit=5");
    expect(r.headers["x-next-cursor"]).toBeUndefined();
  });

  it("pages datasets and jobs the same way", async () => {
    const t = newTenant();
    const c = api(h, t);
    const { project, dataset } = await seedDataset(h, t);
    await c.post(`/v1/projects/${project.id}/datasets`, { name: "d2" });
    await c.post(`/v1/projects/${project.id}/datasets`, { name: "d3" });
    for (let i = 0; i < 3; i++) await c.post(`/v1/datasets/${dataset.id}/jobs`, { label: `j${i}` });

    const datasetsAll = (await c.get(`/v1/projects/${project.id}/datasets`)).body;
    expect((await walk(c, `/v1/projects/${project.id}/datasets`, 1)).ids).toEqual(
      datasetsAll.map((d: { id: string }) => d.id),
    );
    const jobsAll = (await c.get(`/v1/projects/${project.id}/jobs`)).body;
    expect(jobsAll).toHaveLength(3);
    expect((await walk(c, `/v1/projects/${project.id}/jobs`, 2)).ids).toEqual(
      jobsAll.map((j: { id: string }) => j.id),
    );
  });

  it("refuses a malformed cursor and an out-of-range limit", async () => {
    const c = api(h, newTenant());
    const bad = await c.get("/v1/projects?cursor=not-a-cursor");
    expect(bad.status).toBe(400);
    expect(bad.body.error.code).toBe("INVALID_CURSOR");
    expect((await c.get("/v1/projects?limit=0")).status).toBe(400);
    expect((await c.get("/v1/projects?limit=5000")).status).toBe(400);
  });

  it("never returns another tenant's rows, even with a cursor taken from one's own list", async () => {
    const a = api(h, newTenant());
    const b = api(h, newTenant());
    for (let i = 0; i < 3; i++) await a.post("/v1/projects", { name: `a${i}` });
    await b.post("/v1/projects", { name: "b0" });
    const first = await a.get("/v1/projects?limit=1");
    const cursor = first.headers["x-next-cursor"] as string;
    const other = await b.get(`/v1/projects?limit=5&cursor=${cursor}`);
    expect(other.body.map((p: { name: string }) => p.name)).not.toContain("a0");
    expect(other.body.every((p: { name: string }) => p.name.startsWith("b"))).toBe(true);
  });
});
