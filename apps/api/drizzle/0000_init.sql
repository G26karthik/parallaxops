CREATE TABLE "artifacts" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"job_id" uuid NOT NULL,
	"tenant_id" text NOT NULL,
	"attempt_no" integer NOT NULL,
	"kind" text NOT NULL,
	"object_key" text NOT NULL,
	"bytes" bigint NOT NULL,
	"sha256" text NOT NULL,
	"content_type" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "artifacts_job_attempt_kind" UNIQUE("job_id","attempt_no","kind")
);
--> statement-breakpoint
CREATE TABLE "audit_log" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"tenant_id" text NOT NULL,
	"action" text NOT NULL,
	"entity" text NOT NULL,
	"entity_id" text NOT NULL,
	"request_id" text,
	"detail" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "datasets" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"tenant_id" text NOT NULL,
	"project_id" uuid NOT NULL,
	"name" text NOT NULL,
	"preflight_status" text DEFAULT 'NONE' NOT NULL,
	"preflight_report" jsonb,
	"preflight_error" jsonb,
	"preflight_updated_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "images" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"tenant_id" text NOT NULL,
	"dataset_id" uuid NOT NULL,
	"filename" text NOT NULL,
	"content_type" text NOT NULL,
	"size_bytes" bigint NOT NULL,
	"sha256" text NOT NULL,
	"object_key" text NOT NULL,
	"status" text DEFAULT 'PENDING' NOT NULL,
	"reject_reason" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "job_attempts" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"job_id" uuid NOT NULL,
	"tenant_id" text NOT NULL,
	"attempt_no" integer NOT NULL,
	"state" text DEFAULT 'RUNNING' NOT NULL,
	"worker_id" text,
	"started_at" timestamp with time zone,
	"finished_at" timestamp with time zone,
	"failure" jsonb,
	CONSTRAINT "job_attempts_job_no" UNIQUE("job_id","attempt_no")
);
--> statement-breakpoint
CREATE TABLE "job_events" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"job_id" uuid NOT NULL,
	"tenant_id" text NOT NULL,
	"attempt_no" integer NOT NULL,
	"seq" integer NOT NULL,
	"type" text NOT NULL,
	"stage" text,
	"from_state" text,
	"to_state" text,
	"payload" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "job_events_unique" UNIQUE("job_id","attempt_no","seq")
);
--> statement-breakpoint
CREATE TABLE "job_stages" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"job_id" uuid NOT NULL,
	"tenant_id" text NOT NULL,
	"attempt_no" integer NOT NULL,
	"name" text NOT NULL,
	"state" text NOT NULL,
	"started_at" timestamp with time zone,
	"finished_at" timestamp with time zone,
	"seconds" real,
	CONSTRAINT "job_stages_unique" UNIQUE("job_id","attempt_no","name")
);
--> statement-breakpoint
CREATE TABLE "jobs" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"tenant_id" text NOT NULL,
	"project_id" uuid NOT NULL,
	"dataset_id" uuid NOT NULL,
	"state" text DEFAULT 'CREATED' NOT NULL,
	"label" text,
	"idempotency_key" text,
	"request_hash" text,
	"config" jsonb NOT NULL,
	"input_manifest_key" text,
	"workflow_id" text,
	"workflow_started_at" timestamp with time zone,
	"cancel_requested_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"queued_at" timestamp with time zone,
	"started_at" timestamp with time zone,
	"finished_at" timestamp with time zone,
	"last_heartbeat_at" timestamp with time zone,
	"queue_wait_seconds" real,
	"duration_seconds" real,
	"quality" jsonb,
	"metrics" jsonb,
	"ground_truth" jsonb,
	"timings" jsonb,
	"resources" jsonb,
	"warnings" jsonb DEFAULT '[]'::jsonb NOT NULL,
	"diagnostic" jsonb,
	"artifacts_published_at" timestamp with time zone,
	CONSTRAINT "jobs_tenant_idem" UNIQUE("tenant_id","idempotency_key")
);
--> statement-breakpoint
CREATE TABLE "projects" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"tenant_id" text NOT NULL,
	"name" text NOT NULL,
	"description" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "tenants" (
	"id" text PRIMARY KEY NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"max_storage_bytes" bigint DEFAULT 5000000000 NOT NULL,
	"max_active_jobs" integer DEFAULT 2 NOT NULL,
	"max_queued_jobs" integer DEFAULT 5 NOT NULL
);
--> statement-breakpoint
ALTER TABLE "artifacts" ADD CONSTRAINT "artifacts_job_id_jobs_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."jobs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "datasets" ADD CONSTRAINT "datasets_project_id_projects_id_fk" FOREIGN KEY ("project_id") REFERENCES "public"."projects"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "images" ADD CONSTRAINT "images_dataset_id_datasets_id_fk" FOREIGN KEY ("dataset_id") REFERENCES "public"."datasets"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "job_attempts" ADD CONSTRAINT "job_attempts_job_id_jobs_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."jobs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "job_events" ADD CONSTRAINT "job_events_job_id_jobs_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."jobs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "job_stages" ADD CONSTRAINT "job_stages_job_id_jobs_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."jobs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "jobs" ADD CONSTRAINT "jobs_project_id_projects_id_fk" FOREIGN KEY ("project_id") REFERENCES "public"."projects"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "jobs" ADD CONSTRAINT "jobs_dataset_id_datasets_id_fk" FOREIGN KEY ("dataset_id") REFERENCES "public"."datasets"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "audit_tenant_idx" ON "audit_log" USING btree ("tenant_id","at");--> statement-breakpoint
CREATE INDEX "datasets_tenant_project_idx" ON "datasets" USING btree ("tenant_id","project_id");--> statement-breakpoint
CREATE INDEX "images_dataset_idx" ON "images" USING btree ("tenant_id","dataset_id");--> statement-breakpoint
CREATE UNIQUE INDEX "images_dataset_sha_live" ON "images" USING btree ("dataset_id","sha256") WHERE "images"."status" <> 'REJECTED';--> statement-breakpoint
CREATE INDEX "jobs_tenant_project_idx" ON "jobs" USING btree ("tenant_id","project_id","created_at");--> statement-breakpoint
CREATE INDEX "jobs_tenant_state_idx" ON "jobs" USING btree ("tenant_id","state");--> statement-breakpoint
CREATE INDEX "projects_tenant_idx" ON "projects" USING btree ("tenant_id","created_at");