import { describe, expect, it } from "vitest";
import { loadConfig } from "../src/config.ts";

const prod = {
  NODE_ENV: "production",
  INTERNAL_API_SECRET: "a-real-secret-0123456789",
  S3_ACCESS_KEY: "AKIAREALKEY",
  S3_SECRET_KEY: "a-real-s3-secret-value",
};

describe("production configuration", () => {
  it("accepts explicit secrets", () => {
    expect(loadConfig(prod).NODE_ENV).toBe("production");
  });

  it("refuses every development default that is a credential", () => {
    expect(() => loadConfig({ ...prod, INTERNAL_API_SECRET: undefined })).toThrow(
      /INTERNAL_API_SECRET/,
    );
    expect(() => loadConfig({ ...prod, S3_ACCESS_KEY: undefined })).toThrow(/S3_ACCESS_KEY/);
    expect(() => loadConfig({ ...prod, S3_SECRET_KEY: undefined })).toThrow(/S3_SECRET_KEY/);
  });

  it("leaves development and test on the defaults", () => {
    expect(loadConfig({ NODE_ENV: "development" }).S3_ACCESS_KEY).toBe("dev-access-key");
  });
});
