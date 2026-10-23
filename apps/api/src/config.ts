import { z } from "zod";

const Env = z.object({
  NODE_ENV: z.enum(["development", "test", "production"]).default("development"),
  PORT: z.coerce.number().int().default(4000),
  HOST: z.string().default("127.0.0.1"),
  LOG_LEVEL: z.string().default("info"),
  DATABASE_URL: z.string().default("postgres://parallaxops:parallaxops@127.0.0.1:5433/parallaxops"),
  S3_ENDPOINT: z.string().default("http://127.0.0.1:8333"),
  /** Endpoint embedded in presigned URLs. Must be reachable by the browser. */
  S3_PUBLIC_ENDPOINT: z.string().optional(),
  S3_BUCKET: z.string().default("parallaxops"),
  S3_REGION: z.string().default("us-east-1"),
  S3_ACCESS_KEY: z.string().default("dev-access-key"),
  S3_SECRET_KEY: z.string().default("dev-secret-key-change-me-locally"),
  TEMPORAL_ADDRESS: z.string().default("127.0.0.1:7233"),
  TEMPORAL_NAMESPACE: z.string().default("default"),
  INTERNAL_API_SECRET: z.string().min(16).default("change-me-locally-please"),
  CORS_ORIGINS: z.string().default("http://localhost:3000,http://127.0.0.1:3000"),
  /** Days to keep artifacts of finished jobs before the janitor removes them. */
  ARTIFACT_RETENTION_DAYS: z.coerce.number().default(30),
  /** Minutes before an upload that was never completed is garbage collected. */
  PENDING_UPLOAD_TTL_MINUTES: z.coerce.number().default(60),
});

export type Config = z.infer<typeof Env> & { s3PublicEndpoint: string; corsOrigins: string[] };

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const c = Env.parse(env);
  if (c.NODE_ENV === "production" && c.INTERNAL_API_SECRET === "change-me-locally-please") {
    throw new Error("INTERNAL_API_SECRET must be set in production");
  }
  return {
    ...c,
    s3PublicEndpoint: c.S3_PUBLIC_ENDPOINT ?? c.S3_ENDPOINT,
    corsOrigins: c.CORS_ORIGINS.split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  };
}
