import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { type Harness, harness } from "./helpers.ts";

let h: Harness;
beforeAll(async () => {
  h = await harness();
});
afterAll(() => h.close());

describe("generated OpenAPI contract", () => {
  it("docs/openapi.json matches the routes (run `pnpm openapi` after changing a schema)", () => {
    const committed = JSON.parse(
      readFileSync(fileURLToPath(new URL("../../../docs/openapi.json", import.meta.url)), "utf8"),
    );
    expect(JSON.parse(JSON.stringify(h.app.swagger()))).toEqual(committed);
  });

  it("documents every versioned route with an operation tag", () => {
    const spec = h.app.swagger() as { paths: Record<string, Record<string, { tags?: string[] }>> };
    for (const [path, ops] of Object.entries(spec.paths)) {
      expect(path.startsWith("/v1/"), path).toBe(true);
      for (const op of Object.values(ops)) expect(op.tags?.length, path).toBeGreaterThan(0);
    }
  });
});
