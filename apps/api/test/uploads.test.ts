import { keys } from "@parallaxops/contracts";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { api, type Harness, harness, jpeg, newTenant, seedDataset, sha } from "./helpers.ts";

let h: Harness;
beforeAll(async () => {
  h = await harness();
});
afterAll(() => h.close());

async function dataset(t: string) {
  const c = api(h, t);
  const p = (await c.post("/v1/projects", { name: "p" })).body;
  return (await c.post(`/v1/projects/${p.id}/datasets`, { name: "d" })).body;
}
const file = (name: string, bytes: Uint8Array, contentType = "image/jpeg") => ({
  filename: name,
  contentType,
  sizeBytes: bytes.length,
  sha256: sha(bytes),
});

describe("upload requests", () => {
  it("sanitizes hostile filenames and keeps them out of object keys", async () => {
    const t = newTenant();
    const d = await dataset(t);
    const b = jpeg(1);
    const r = await api(h, t).post(`/v1/datasets/${d.id}/uploads`, {
      files: [file("../../../etc/passwd\u0000.jpg", b)],
    });
    expect(r.status).toBe(201);
    const u = r.body.uploads[0];
    expect(u.filename).not.toMatch(/[\\/\u0000]|\.\./);
    expect(decodeURIComponent(u.url)).toContain(
      `tenants/${t}/datasets/${d.id}/images/${u.imageId}.jpg`,
    );
    expect(decodeURIComponent(u.url)).not.toContain("passwd");
    expect(u.headers["content-type"]).toBe("image/jpeg");
  });

  it.each([
    ["wrong content type", { contentType: "application/x-msdownload" }],
    ["svg", { contentType: "image/svg+xml" }],
    ["zero size", { sizeBytes: 0 }],
    ["huge size", { sizeBytes: 10 * 1024 ** 3 }],
    ["bad sha", { sha256: "XYZ" }],
  ])("rejects %s with a validation error", async (_n, patch) => {
    const t = newTenant();
    const d = await dataset(t);
    const r = await api(h, t).post(`/v1/datasets/${d.id}/uploads`, {
      files: [{ ...file("a.jpg", jpeg(2)), ...patch }],
    });
    expect(r.status).toBe(400);
    expect(r.body.error.code).toBe("VALIDATION_ERROR");
  });

  it("deduplicates by SHA-256 within a dataset and within one batch", async () => {
    const t = newTenant();
    const d = await dataset(t);
    const c = api(h, t);
    const b = jpeg(3);
    const first = await c.post(`/v1/datasets/${d.id}/uploads`, {
      files: [file("a.jpg", b), file("copy-of-a.jpg", b)],
    });
    expect(first.body.uploads.map((u: { action: string }) => u.action)).toEqual([
      "UPLOAD",
      "DUPLICATE",
    ]);
    expect(first.body.uploads[1].imageId).toBe(first.body.uploads[0].imageId);
    const again = await c.post(`/v1/datasets/${d.id}/uploads`, { files: [file("renamed.jpg", b)] });
    expect(again.body.uploads[0].action).toBe("DUPLICATE");
    expect(again.body.uploads[0].url).toBeNull();
  });

  it("enforces the per-dataset file limit with 413", async () => {
    const t = newTenant();
    const d = await dataset(t);
    const files = Array.from({ length: 80 }, (_, i) => file(`${i}.jpg`, jpeg(i + 10, 80)));
    const ok = await api(h, t).post(`/v1/datasets/${d.id}/uploads`, { files });
    expect(ok.status).toBe(201);
    const over = await api(h, t).post(`/v1/datasets/${d.id}/uploads`, {
      files: [file("extra.jpg", jpeg(250, 99))],
    });
    expect(over.status).toBe(413);
    expect(over.body.error.code).toBe("TOO_MANY_FILES");
  });
});

describe("upload completion checks", () => {
  async function pending(t: string, bytes: Uint8Array, declared = bytes) {
    const d = await dataset(t);
    const c = api(h, t);
    const r = await c.post(`/v1/datasets/${d.id}/uploads`, { files: [file("a.jpg", declared)] });
    const id = r.body.uploads[0].imageId;
    return { c, d, id, key: keys.image(t, d.id, id, "jpg") };
  }

  it("accepts a valid object and marks it UPLOADED", async () => {
    const t = newTenant();
    const b = jpeg(5);
    const { c, d, id, key } = await pending(t, b);
    h.store.putBytes(key, b);
    const r = await c.post(`/v1/datasets/${d.id}/uploads/complete`, { imageIds: [id] });
    expect(r.body.images[0].status).toBe("UPLOADED");
    expect((await c.get(`/v1/datasets/${d.id}`)).body.imageCount).toBe(1);
  });

  it("keeps the image PENDING when the object has not arrived yet", async () => {
    const t = newTenant();
    const { c, d, id } = await pending(t, jpeg(6));
    const r = await c.post(`/v1/datasets/${d.id}/uploads/complete`, { imageIds: [id] });
    expect(r.body.images[0].status).toBe("PENDING");
  });

  it("rejects and deletes content that is not the declared type (magic bytes)", async () => {
    const t = newTenant();
    const declared = jpeg(7);
    const { c, d, id, key } = await pending(t, declared);
    const exe = new Uint8Array(declared.length).fill(0x4d); // not JPEG
    h.store.putBytes(key, exe);
    const r = await c.post(`/v1/datasets/${d.id}/uploads/complete`, { imageIds: [id] });
    expect(r.body.images[0].status).toBe("REJECTED");
    expect(r.body.images[0].rejectReason).toMatch(/^bad_magic/);
    expect(h.store.objects.has(key)).toBe(false);
  });

  it("rejects a size mismatch", async () => {
    const t = newTenant();
    const declared = jpeg(8, 64);
    const { c, d, id, key } = await pending(t, declared);
    h.store.putBytes(key, jpeg(8, 65));
    const r = await c.post(`/v1/datasets/${d.id}/uploads/complete`, { imageIds: [id] });
    expect(r.body.images[0].rejectReason).toMatch(/^size_mismatch/);
  });

  it("rejects bytes whose checksum differs from the declared SHA-256", async () => {
    const t = newTenant();
    const declared = jpeg(9, 64);
    const { c, d, id, key } = await pending(t, declared);
    const other = jpeg(9, 64);
    other[10] = 0x01; // same length and magic, different content
    h.store.putBytes(key, other);
    const r = await c.post(`/v1/datasets/${d.id}/uploads/complete`, { imageIds: [id] });
    expect(r.body.images[0].rejectReason).toMatch(/^checksum_mismatch/);
  });

  it("is idempotent: completing twice returns the same UPLOADED image", async () => {
    const t = newTenant();
    const { dataset, imageIds } = await seedDataset(h, t, 5);
    const again = await api(h, t).post(`/v1/datasets/${dataset.id}/uploads/complete`, {
      imageIds: [imageIds[0]],
    });
    expect(again.body.images[0].status).toBe("UPLOADED");
  });
});
