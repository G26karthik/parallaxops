import { z } from "zod";
import { ALLOWED_CONTENT_TYPES, LIMITS } from "./limits.ts";
import { JobStateSchema } from "./state.ts";

export const CONTRACT_VERSION = "1.0";

const Iso = z.iso.datetime({ offset: true });

export const DIAGNOSTIC_CODES = [
  "TOO_FEW_IMAGES",
  "TOO_MANY_IMAGES",
  "UNREADABLE_IMAGES",
  "INPUT_TOO_LARGE",
  "DUPLICATE_IMAGES",
  "NO_FEATURES",
  "INSUFFICIENT_MATCHES",
  "NO_INITIAL_PAIR",
  "SFM_NO_MODEL",
  "LOW_REGISTRATION",
  "FRAGMENTED_MODEL",
  "TIMEOUT",
  "MEMORY_BUDGET_EXCEEDED",
  "DISK_BUDGET_EXCEEDED",
  "CANCELLED",
  "ENGINE_CRASH",
  "INTERNAL",
] as const;

export const DiagnosticSchema = z.object({
  code: z.enum(DIAGNOSTIC_CODES),
  message: z.string(),
  advice: z.string(),
  stage: z.string().nullish(),
  // Optional (not defaulted): response serialization does not apply Zod defaults.
  details: z.record(z.string(), z.unknown()).optional(),
});
export type Diagnostic = z.infer<typeof DiagnosticSchema>;

// ---------- user-facing reconstruction config (bounded to protect the host) ----------
export const ReconstructionConfigSchema = z.object({
  maxImageSize: z.number().int().min(480).max(3200).default(1600),
  maxNumFeatures: z.number().int().min(1024).max(16384).default(8192),
  mapper: z.enum(["incremental", "global"]).default("incremental"),
  matching: z.enum(["auto", "exhaustive", "sequential"]).default("auto"),
  cameraModel: z
    .enum(["SIMPLE_RADIAL", "RADIAL", "OPENCV", "SIMPLE_PINHOLE"])
    .default("SIMPLE_RADIAL"),
  seed: z
    .number()
    .int()
    .min(0)
    .max(2 ** 31 - 1)
    .default(0),
});
export type ReconstructionConfig = z.infer<typeof ReconstructionConfigSchema>;

// ---------- projects ----------
export const ProjectSchema = z.object({
  id: z.uuid(),
  name: z.string(),
  description: z.string().nullable(),
  createdAt: Iso,
});
export const CreateProjectBody = z.object({
  name: z.string().trim().min(1).max(80),
  description: z.string().trim().max(500).optional(),
});

// ---------- datasets / uploads ----------
export const PreflightSchema = z.object({
  status: z.enum(["NONE", "RUNNING", "DONE", "FAILED"]),
  report: z.record(z.string(), z.unknown()).nullable(),
  error: DiagnosticSchema.nullable(),
  updatedAt: Iso.nullable(),
});
export const DatasetSchema = z.object({
  id: z.uuid(),
  projectId: z.uuid(),
  name: z.string(),
  imageCount: z.number().int(),
  totalBytes: z.number().int(),
  preflight: PreflightSchema,
  createdAt: Iso,
});
export const CreateDatasetBody = z.object({ name: z.string().trim().min(1).max(80) });

export const ImageSchema = z.object({
  id: z.uuid(),
  filename: z.string(),
  contentType: z.enum(ALLOWED_CONTENT_TYPES),
  sizeBytes: z.number().int(),
  sha256: z.string().regex(/^[0-9a-f]{64}$/),
  status: z.enum(["PENDING", "UPLOADED", "REJECTED"]),
  rejectReason: z.string().nullable(),
  createdAt: Iso,
});

export const UploadFileSchema = z.object({
  filename: z.string().min(1).max(400),
  contentType: z.enum(ALLOWED_CONTENT_TYPES),
  sizeBytes: z.number().int().min(1).max(LIMITS.maxImageBytes),
  sha256: z.string().regex(/^[0-9a-f]{64}$/, "sha256 must be 64 lowercase hex chars"),
});
export const RequestUploadsBody = z.object({
  files: z.array(UploadFileSchema).min(1).max(LIMITS.maxFilesPerDataset),
});
export const UploadTargetSchema = z.object({
  imageId: z.uuid(),
  filename: z.string(),
  sha256: z.string(),
  action: z.enum(["UPLOAD", "DUPLICATE"]),
  url: z.string().nullable(),
  headers: z.record(z.string(), z.string()),
  expiresAt: Iso.nullable(),
});
export const RequestUploadsResponse = z.object({ uploads: z.array(UploadTargetSchema) });
export const CompleteUploadsBody = z.object({
  imageIds: z.array(z.uuid()).min(1).max(LIMITS.maxFilesPerDataset),
});
export const CompleteUploadsResponse = z.object({
  images: z.array(ImageSchema),
});

// ---------- jobs ----------
export const QualityVerdictSchema = z.object({
  level: z.enum(["good", "fair", "poor"]),
  reasons: z.array(z.string()),
  rubric: z.string().optional(),
});

export const StageSchema = z.object({
  name: z.string(),
  state: z.enum(["RUNNING", "DONE", "FAILED"]),
  startedAt: Iso.nullable(),
  finishedAt: Iso.nullable(),
  seconds: z.number().nullable(),
});
export const AttemptSchema = z.object({
  attemptNo: z.number().int(),
  state: z.enum(["RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]),
  workerId: z.string().nullable(),
  startedAt: Iso.nullable(),
  finishedAt: Iso.nullable(),
  failure: DiagnosticSchema.nullable(),
});
export const ArtifactRefSchema = z.object({
  kind: z.string(),
  bytes: z.number().int(),
  sha256: z.string(),
  contentType: z.string(),
});

export const JobSchema = z.object({
  id: z.uuid(),
  projectId: z.uuid(),
  datasetId: z.uuid(),
  state: JobStateSchema,
  config: ReconstructionConfigSchema,
  label: z.string().nullable(),
  createdAt: Iso,
  queuedAt: Iso.nullable(),
  startedAt: Iso.nullable(),
  finishedAt: Iso.nullable(),
  queueWaitSeconds: z.number().nullable(),
  durationSeconds: z.number().nullable(),
  quality: QualityVerdictSchema.nullable(),
  /** Mirrors the worker's report.json `metrics` block. Loose on purpose: contract minor versions add fields. */
  metrics: z.record(z.string(), z.unknown()).nullable(),
  groundTruth: z.record(z.string(), z.unknown()).nullable(),
  timings: z.record(z.string(), z.number()).nullable(),
  resources: z.record(z.string(), z.unknown()).nullable(),
  warnings: z.array(DiagnosticSchema),
  diagnostic: DiagnosticSchema.nullable(),
  stages: z.array(StageSchema),
  attempts: z.array(AttemptSchema),
  artifacts: z.array(ArtifactRefSchema),
});
export type Job = z.infer<typeof JobSchema>;

export const CreateJobBody = z.object({
  config: ReconstructionConfigSchema.partial().optional(),
  label: z.string().trim().max(80).optional(),
});
export const JobCreatedResponse = z.object({
  id: z.uuid(),
  state: JobStateSchema,
  idempotentReplay: z.boolean(),
});

export const ArtifactLinkSchema = ArtifactRefSchema.extend({
  filename: z.string(),
  url: z.string(),
  expiresAt: Iso,
});
export const SceneSchema = z.object({
  jobId: z.uuid(),
  pointCount: z.number().int(),
  pointsUrl: z.string(),
  camerasUrl: z.string(),
  expiresAt: Iso,
  note: z.string(),
});

export const ErrorSchema = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    requestId: z.string(),
    details: z.unknown().optional(),
  }),
});
export type ApiError = z.infer<typeof ErrorSchema>;

// ---------- worker -> API (internal, shared-secret) ----------
export const WorkerEventSchema = z.object({
  jobId: z.uuid(),
  attemptNo: z.number().int().min(1),
  /** Monotonic per (job, attempt). The API ignores replays of a seq it already stored. */
  seq: z.number().int().min(1),
  type: z.enum([
    "attempt_started",
    "attempt_failed",
    "stage_started",
    "stage_finished",
    "heartbeat",
    "completed",
    "failed",
    "cancelled",
  ]),
  state: JobStateSchema.optional(),
  stage: z.string().optional(),
  workerId: z.string().max(120).optional(),
  payload: z.record(z.string(), z.unknown()).default({}),
});
export type WorkerEvent = z.infer<typeof WorkerEventSchema>;

export const PreflightResultSchema = z.object({
  datasetId: z.uuid(),
  ok: z.boolean(),
  report: z.record(z.string(), z.unknown()).optional(),
  error: DiagnosticSchema.optional(),
});
export type PreflightResult = z.infer<typeof PreflightResultSchema>;

export const ArtifactReportSchema = z.object({
  kind: z.string(),
  key: z.string(),
  bytes: z.number().int(),
  sha256: z.string(),
  contentType: z.string(),
});
export type ArtifactReport = z.infer<typeof ArtifactReportSchema>;
