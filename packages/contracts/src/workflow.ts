import { z } from "zod";
import { ReconstructionConfigSchema } from "./api.ts";
import { TenantIdSchema } from "./limits.ts";

/**
 * Cross-language workflow contract between the TypeScript API (starts workflows) and the Python
 * worker (implements them). Only IDs, object keys and small config travel through Temporal history;
 * image bytes and artifacts never do. Bump `WORKFLOW_CONTRACT` on any breaking change and keep the
 * worker accepting the previous major during a rolling upgrade (see docs/VERSIONING.md).
 */
export const WORKFLOW_CONTRACT = 1;

export const WORKFLOW_NAMES = {
  reconstruct: "ReconstructionWorkflow",
  preflight: "PreflightWorkflow",
} as const;

export const TASK_QUEUES = {
  workflows: "parallaxops-workflows",
  /** CPU/GPU-heavy reconstruction. Per-worker max_concurrent_activities = 1. */
  heavy: "vision-heavy",
  light: "vision-light",
} as const;

export const ReconstructionWorkflowInput = z.object({
  contract: z.literal(WORKFLOW_CONTRACT),
  jobId: z.uuid(),
  tenantId: TenantIdSchema,
  datasetId: z.uuid(),
  /** S3 key of the input manifest written by the API at job creation. */
  inputManifestKey: z.string(),
  config: ReconstructionConfigSchema,
});
export type ReconstructionWorkflowInput = z.infer<typeof ReconstructionWorkflowInput>;

export const PreflightWorkflowInput = z.object({
  contract: z.literal(WORKFLOW_CONTRACT),
  tenantId: TenantIdSchema,
  datasetId: z.uuid(),
  inputManifestKey: z.string(),
});
export type PreflightWorkflowInput = z.infer<typeof PreflightWorkflowInput>;

export const InputManifest = z.object({
  contract: z.literal(WORKFLOW_CONTRACT),
  tenantId: TenantIdSchema,
  datasetId: z.uuid(),
  jobId: z.uuid().nullable(),
  outputPrefix: z.string(),
  images: z.array(
    z.object({
      imageId: z.uuid(),
      filename: z.string(),
      key: z.string(),
      sha256: z.string(),
      bytes: z.number().int(),
      contentType: z.string(),
    }),
  ),
});
export type InputManifest = z.infer<typeof InputManifest>;

/** Object layout. Every key is tenant-prefixed so one policy/prefix scan can never cross tenants. */
export const keys = {
  image: (tenant: string, dataset: string, imageId: string, ext: string) =>
    `tenants/${tenant}/datasets/${dataset}/images/${imageId}.${ext}`,
  inputManifest: (tenant: string, dataset: string, ref: string) =>
    `tenants/${tenant}/datasets/${dataset}/manifests/${ref}.json`,
  jobRoot: (tenant: string, job: string) => `tenants/${tenant}/jobs/${job}/`,
  /** Attempt-specific prefix: a retried attempt can never overwrite an earlier attempt's artifacts. */
  jobPrefix: (tenant: string, job: string, attempt: number) =>
    `tenants/${tenant}/jobs/${job}/attempt-${attempt}/`,
};
