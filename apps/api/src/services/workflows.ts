import {
  type PreflightWorkflowInput,
  type ReconstructionWorkflowInput,
  TASK_QUEUES,
  WORKFLOW_NAMES,
} from "@parallaxops/contracts";
import { Client, Connection, WorkflowIdConflictPolicy } from "@temporalio/client";
import type { Config } from "../config.ts";

/** Seam between the control plane and the workflow engine; faked in unit tests. */
export interface WorkflowStarter {
  /** Idempotent: starting an already-running workflow id is a no-op. */
  startReconstruction(workflowId: string, input: ReconstructionWorkflowInput): Promise<void>;
  startPreflight(workflowId: string, input: PreflightWorkflowInput): Promise<void>;
  /** Returns false if the workflow no longer exists / already finished. */
  cancel(workflowId: string): Promise<boolean>;
  ping(): Promise<void>;
}

export class TemporalStarter implements WorkflowStarter {
  private readonly client: Client;

  private constructor(client: Client) {
    this.client = client;
  }

  static async connect(cfg: Config): Promise<TemporalStarter> {
    const connection = await Connection.connect({ address: cfg.TEMPORAL_ADDRESS });
    return new TemporalStarter(new Client({ connection, namespace: cfg.TEMPORAL_NAMESPACE }));
  }

  async startReconstruction(workflowId: string, input: ReconstructionWorkflowInput) {
    await this.client.workflow.start(WORKFLOW_NAMES.reconstruct, {
      taskQueue: TASK_QUEUES.workflows,
      workflowId,
      args: [input],
      workflowIdConflictPolicy: WorkflowIdConflictPolicy.USE_EXISTING,
    });
  }

  async startPreflight(workflowId: string, input: PreflightWorkflowInput) {
    await this.client.workflow.start(WORKFLOW_NAMES.preflight, {
      taskQueue: TASK_QUEUES.workflows,
      workflowId,
      args: [input],
      workflowIdConflictPolicy: WorkflowIdConflictPolicy.USE_EXISTING,
    });
  }

  async cancel(workflowId: string) {
    try {
      await this.client.workflow.getHandle(workflowId).cancel();
      return true;
    } catch (e) {
      if ((e as { name?: string }).name === "WorkflowNotFoundError") return false;
      throw e;
    }
  }

  async ping() {
    await this.client.workflowService.getSystemInfo({});
  }
}

/** Records calls; can be told to fail to test the reconciler. */
export class FakeStarter implements WorkflowStarter {
  readonly started: { id: string; kind: "reconstruct" | "preflight"; input: unknown }[] = [];
  readonly cancelled: string[] = [];
  failNext = 0;

  private maybeFail() {
    if (this.failNext > 0) {
      this.failNext--;
      throw new Error("workflow engine unavailable");
    }
  }
  async startReconstruction(id: string, input: ReconstructionWorkflowInput) {
    this.maybeFail();
    if (!this.started.some((s) => s.id === id))
      this.started.push({ id, kind: "reconstruct", input });
  }
  async startPreflight(id: string, input: PreflightWorkflowInput) {
    this.maybeFail();
    this.started.push({ id, kind: "preflight", input });
  }
  async cancel(id: string) {
    this.cancelled.push(id);
    return true;
  }
  async ping() {}
}
