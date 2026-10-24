import { describe, expect, it } from "vitest";
import { loadConfig } from "../src/config.ts";
import { S3Store } from "../src/services/storage.ts";

describe("presigned upload URL", () => {
  it("signs the declared size and digest, so a different body is refused by the object store", async () => {
    const store = new S3Store(loadConfig({}));
    const sha = "ab".repeat(32);
    const url = new URL(await store.presignPut("tenants/t/a.jpg", "image/jpeg", 1234, sha, 900));
    const signed = (url.searchParams.get("X-Amz-SignedHeaders") ?? "").split(";");
    expect(signed).toContain("content-length");
    expect(signed).toContain("content-type");
    // A header the client must send, not a query parameter it could drop or change.
    expect(signed).toContain("x-amz-checksum-sha256");
    expect(url.searchParams.has("x-amz-checksum-sha256")).toBe(false);
  });
});
