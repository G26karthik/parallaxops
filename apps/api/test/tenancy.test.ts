import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  api,
  completeJob,
  event,
  type Harness,
  harness,
  newTenant,
  SECRET,
  seedDataset,
} from "./helpers.ts";

let h: Harness;
beforeAll(async () => {
  h = await harness();
});
afterAll(() => h.close());

describe("tenant scoping", () => {
  it("requires a valid X-Tenant-Id on every /v1 route", async () => {
    const r = await h.app.inject({ method: "GET", url: "/v1/projects" });
    expect(r.statusCode).toBe(400);
    expect(JSON.parse(r.body).error.code).toBe("TENANT_REQUIRED");
    const bad = await h.app.inject({
      method: "GET",
      url: "/v1/projects",
      headers: { "x-tenant-id": "../etc" },
    });
    expect(bad.statusCode).toBe(400);
  });

  it("denies cross-tenant access to every resource with 404 (no existence oracle)", async () => {
    const a = newTenant();
    const b = newTenant();
    const { project, dataset, imageIds } = await seedDataset(h, a);
    const A = api(h, a);
    const job = (await A.post(`/v1/datasets/${dataset.id}/jobs`, {})).body;
    const B = api(h, b);

    expect((await B.get("/v1/projects")).body).toEqual([]);
    for (const url of [
      `/v1/projects/${project.id}`,
      `/v1/projects/${project.id}/datasets`,
      `/v1/datasets/${dataset.id}`,
      `/v1/datasets/${dataset.id}/images`,
      `/v1/jobs/${job.id}`,
      `/v1/jobs/${job.id}/events`,
      `/v1/jobs/${job.id}/artifacts`,
      `/v1/jobs/${job.id}/scene`,
    ]) {
      const r = await B.get(url);
      // list endpoints scoped by tenant return empty arrays; everything else must be 404
      expect(
        r.status === 404 || (r.status === 200 && Array.isArray(r.body) && r.body.length === 0),
        `${url} -> ${r.status}`,
      ).toBe(true);
    }
    expect((await B.post(`/v1/jobs/${job.id}/cancel`)).status).toBe(404);
    expect((await B.post(`/v1/datasets/${dataset.id}/jobs`, {})).status).toBe(404);
    expect(
      (
        await B.post(`/v1/datasets/${dataset.id}/uploads`, {
          files: [
            { filename: "x.jpg", contentType: "image/jpeg", sizeBytes: 10, sha256: "a".repeat(64) },
          ],
        })
      ).status,
    ).toBe(404);
    expect((await B.del(`/v1/datasets/${dataset.id}/images/${imageIds[0]}`)).status).toBe(404);
    expect((await B.post(`/v1/projects/${project.id}/datasets`, { name: "x" })).status).toBe(404);
    // and A still owns everything
    expect((await A.get(`/v1/jobs/${job.id}`)).status).toBe(200);
  });

  it("ignores a tenant id supplied in the body or query", async () => {
    const a = newTenant();
    const b = newTenant();
    const B = api(h, b);
    const r = await B.post("/v1/projects", { name: "mine", tenantId: a });
    expect(r.status).toBe(201);
    expect((await api(h, a).get("/v1/projects")).body).toEqual([]);
    expect((await B.get(`/v1/projects?tenantId=${a}`)).body).toHaveLength(1);
  });

  it("never lets one tenant's presigned artifact links point into another tenant's prefix", async () => {
    const a = newTenant();
    const { dataset } = await seedDataset(h, a);
    const A = api(h, a);
    const job = (await A.post(`/v1/datasets/${dataset.id}/jobs`, {})).body;
    await completeJob(h, job.id, {
      metrics: { n_points3d: 3 },
      artifacts: [
        {
          kind: "points_preview",
          key: `tenants/${a}/jobs/${job.id}/attempt-1/points_preview.bin`,
          bytes: 10,
          sha256: "b".repeat(64),
          contentType: "application/octet-stream",
        },
      ],
    });
    const arts = (await A.get(`/v1/jobs/${job.id}/artifacts`)).body;
    expect(arts).toHaveLength(1);
    expect(decodeURIComponent(arts[0].url)).toContain(`tenants/${a}/`);
    expect((await api(h, newTenant()).get(`/v1/jobs/${job.id}/artifacts`)).status).toBe(404);
  });
});

describe("internal endpoints", () => {
  it("reject missing and wrong secrets, accept the right one", async () => {
    const payload = event(randomUUID(), "heartbeat");
    const none = await h.app.inject({ method: "POST", url: "/internal/v1/events", payload });
    expect(none.statusCode).toBe(401);
    const wrong = await h.app.inject({
      method: "POST",
      url: "/internal/v1/events",
      headers: { "x-internal-secret": "nope-nope-nope-nope" },
      payload,
    });
    expect(wrong.statusCode).toBe(401);
    // right secret reaches the handler (unknown job -> 404, not 401)
    const ok = await h.app.inject({
      method: "POST",
      url: "/internal/v1/events",
      headers: SECRET,
      payload,
    });
    expect(ok.statusCode).toBe(404);
  });

  it("a browser tenant header never grants access to internal routes", async () => {
    const r = await h.app.inject({
      method: "POST",
      url: "/internal/v1/cleanup",
      headers: { "x-tenant-id": newTenant() },
    });
    expect(r.statusCode).toBe(401);
  });
});

describe("error contract", () => {
  it("returns a structured error with a request id and never leaks internals", async () => {
    const t = newTenant();
    const r = await h.app.inject({
      method: "POST",
      url: "/v1/projects",
      headers: { "x-tenant-id": t, "x-request-id": "req-abcdef123" },
      payload: { name: "" },
    });
    expect(r.statusCode).toBe(400);
    const body = JSON.parse(r.body);
    expect(body.error.code).toBe("VALIDATION_ERROR");
    expect(body.error.requestId).toBe("req-abcdef123");
    expect(r.headers["x-request-id"]).toBe("req-abcdef123");
    expect(r.body).not.toMatch(/node_modules|at \w+ \(/);
  });

  it("replaces a malicious request id with a generated one", async () => {
    const r = await h.app.inject({
      method: "GET",
      url: "/healthz",
      headers: { "x-request-id": "bad id with spaces\r\n" },
    });
    expect(r.headers["x-request-id"]).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("publishes an OpenAPI 3.1 document covering the v1 surface", async () => {
    const r = await h.app.inject({ method: "GET", url: "/openapi.json" });
    const spec = JSON.parse(r.body);
    expect(spec.openapi).toMatch(/^3\.1/);
    expect(Object.keys(spec.paths)).toEqual(
      expect.arrayContaining(["/v1/projects", "/v1/datasets/{datasetId}/jobs", "/v1/jobs/{jobId}"]),
    );
  });
});
