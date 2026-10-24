import { sql } from "drizzle-orm";
import {
  bigint,
  bigserial,
  index,
  integer,
  jsonb,
  pgTable,
  real,
  text,
  timestamp,
  unique,
  uniqueIndex,
  uuid,
} from "drizzle-orm/pg-core";

const ts = (name: string) => timestamp(name, { withTimezone: true, mode: "date" });
const id = () => uuid("id").primaryKey().defaultRandom();
const tenantId = () => text("tenant_id").notNull();

export const tenants = pgTable("tenants", {
  id: text("id").primaryKey(),
  createdAt: ts("created_at").notNull().defaultNow(),
  /** Quotas live on the row so they can be tuned per tenant without a deploy. */
  maxStorageBytes: bigint("max_storage_bytes", { mode: "number" }).notNull().default(5_000_000_000),
  maxQueuedJobs: integer("max_queued_jobs").notNull().default(5),
});

export const projects = pgTable(
  "projects",
  {
    id: id(),
    tenantId: tenantId(),
    name: text("name").notNull(),
    description: text("description"),
    createdAt: ts("created_at").notNull().defaultNow(),
  },
  (t) => [index("projects_tenant_idx").on(t.tenantId, t.createdAt)],
);

export const datasets = pgTable(
  "datasets",
  {
    id: id(),
    tenantId: tenantId(),
    projectId: uuid("project_id")
      .notNull()
      .references(() => projects.id, { onDelete: "cascade" }),
    name: text("name").notNull(),
    preflightStatus: text("preflight_status").notNull().default("NONE"),
    preflightReport: jsonb("preflight_report").$type<Record<string, unknown>>(),
    preflightError: jsonb("preflight_error").$type<Record<string, unknown>>(),
    preflightUpdatedAt: ts("preflight_updated_at"),
    createdAt: ts("created_at").notNull().defaultNow(),
  },
  (t) => [index("datasets_tenant_project_idx").on(t.tenantId, t.projectId)],
);

export const images = pgTable(
  "images",
  {
    id: id(),
    tenantId: tenantId(),
    datasetId: uuid("dataset_id")
      .notNull()
      .references(() => datasets.id, { onDelete: "cascade" }),
    filename: text("filename").notNull(),
    contentType: text("content_type").notNull(),
    sizeBytes: bigint("size_bytes", { mode: "number" }).notNull(),
    sha256: text("sha256").notNull(),
    objectKey: text("object_key").notNull(),
    status: text("status").notNull().default("PENDING"), // PENDING | UPLOADED | REJECTED
    rejectReason: text("reject_reason"),
    createdAt: ts("created_at").notNull().defaultNow(),
  },
  (t) => [
    index("images_dataset_idx").on(t.tenantId, t.datasetId),
    // Checksum-based dedup: one live row per content hash per dataset.
    uniqueIndex("images_dataset_sha_live")
      .on(t.datasetId, t.sha256)
      .where(sql`${t.status} <> 'REJECTED'`),
  ],
);

export const jobs = pgTable(
  "jobs",
  {
    id: id(),
    tenantId: tenantId(),
    projectId: uuid("project_id")
      .notNull()
      .references(() => projects.id, { onDelete: "cascade" }),
    datasetId: uuid("dataset_id")
      .notNull()
      .references(() => datasets.id, { onDelete: "cascade" }),
    state: text("state").notNull().default("CREATED"),
    label: text("label"),
    idempotencyKey: text("idempotency_key"),
    requestHash: text("request_hash"),
    config: jsonb("config").$type<Record<string, unknown>>().notNull(),
    inputManifestKey: text("input_manifest_key"),
    workflowId: text("workflow_id"),
    workflowStartedAt: ts("workflow_started_at"),
    cancelRequestedAt: ts("cancel_requested_at"),
    createdAt: ts("created_at").notNull().defaultNow(),
    queuedAt: ts("queued_at"),
    startedAt: ts("started_at"),
    finishedAt: ts("finished_at"),
    lastHeartbeatAt: ts("last_heartbeat_at"),
    queueWaitSeconds: real("queue_wait_seconds"),
    durationSeconds: real("duration_seconds"),
    quality: jsonb("quality").$type<Record<string, unknown>>(),
    metrics: jsonb("metrics").$type<Record<string, unknown>>(),
    groundTruth: jsonb("ground_truth").$type<Record<string, unknown>>(),
    timings: jsonb("timings").$type<Record<string, number>>(),
    resources: jsonb("resources").$type<Record<string, unknown>>(),
    warnings: jsonb("warnings").$type<Record<string, unknown>[]>().notNull().default([]),
    diagnostic: jsonb("diagnostic").$type<Record<string, unknown>>(),
    artifactsPublishedAt: ts("artifacts_published_at"),
  },
  (t) => [
    index("jobs_tenant_project_idx").on(t.tenantId, t.projectId, t.createdAt),
    index("jobs_tenant_state_idx").on(t.tenantId, t.state),
    // Idempotent creation is scoped per tenant.
    unique("jobs_tenant_idem").on(t.tenantId, t.idempotencyKey),
  ],
);

/** One row per execution attempt. Rows are only ever inserted and finished, never rewritten. */
export const jobAttempts = pgTable(
  "job_attempts",
  {
    id: id(),
    jobId: uuid("job_id")
      .notNull()
      .references(() => jobs.id, { onDelete: "cascade" }),
    tenantId: tenantId(),
    attemptNo: integer("attempt_no").notNull(),
    state: text("state").notNull().default("RUNNING"),
    workerId: text("worker_id"),
    startedAt: ts("started_at"),
    finishedAt: ts("finished_at"),
    failure: jsonb("failure").$type<Record<string, unknown>>(),
  },
  (t) => [unique("job_attempts_job_no").on(t.jobId, t.attemptNo)],
);

export const jobStages = pgTable(
  "job_stages",
  {
    id: id(),
    jobId: uuid("job_id")
      .notNull()
      .references(() => jobs.id, { onDelete: "cascade" }),
    tenantId: tenantId(),
    attemptNo: integer("attempt_no").notNull(),
    name: text("name").notNull(),
    state: text("state").notNull(),
    startedAt: ts("started_at"),
    finishedAt: ts("finished_at"),
    seconds: real("seconds"),
  },
  (t) => [unique("job_stages_unique").on(t.jobId, t.attemptNo, t.name)],
);

/** Append-only event log. (job, attempt, seq) is unique: replays are detected and ignored. */
export const jobEvents = pgTable(
  "job_events",
  {
    id: bigserial("id", { mode: "number" }).primaryKey(),
    jobId: uuid("job_id")
      .notNull()
      .references(() => jobs.id, { onDelete: "cascade" }),
    tenantId: tenantId(),
    attemptNo: integer("attempt_no").notNull(),
    seq: integer("seq").notNull(),
    type: text("type").notNull(),
    stage: text("stage"),
    fromState: text("from_state"),
    toState: text("to_state"),
    payload: jsonb("payload").$type<Record<string, unknown>>().notNull().default({}),
    createdAt: ts("created_at").notNull().defaultNow(),
  },
  (t) => [unique("job_events_unique").on(t.jobId, t.attemptNo, t.seq)],
);

export const artifacts = pgTable(
  "artifacts",
  {
    id: id(),
    jobId: uuid("job_id")
      .notNull()
      .references(() => jobs.id, { onDelete: "cascade" }),
    tenantId: tenantId(),
    attemptNo: integer("attempt_no").notNull(),
    kind: text("kind").notNull(),
    objectKey: text("object_key").notNull(),
    bytes: bigint("bytes", { mode: "number" }).notNull(),
    sha256: text("sha256").notNull(),
    contentType: text("content_type").notNull(),
    createdAt: ts("created_at").notNull().defaultNow(),
  },
  (t) => [unique("artifacts_job_attempt_kind").on(t.jobId, t.attemptNo, t.kind)],
);

/** One row per periodic task: which API replica runs it now, and until when (services/lease.ts). */
export const maintenanceLeases = pgTable("maintenance_leases", {
  name: text("name").primaryKey(),
  holder: text("holder").notNull(),
  expiresAt: ts("expires_at").notNull(),
});

export const auditLog = pgTable(
  "audit_log",
  {
    id: bigserial("id", { mode: "number" }).primaryKey(),
    tenantId: tenantId(),
    action: text("action").notNull(),
    entity: text("entity").notNull(),
    entityId: text("entity_id").notNull(),
    requestId: text("request_id"),
    detail: jsonb("detail").$type<Record<string, unknown>>().notNull().default({}),
    at: ts("at").notNull().defaultNow(),
  },
  (t) => [index("audit_tenant_idx").on(t.tenantId, t.at)],
);
