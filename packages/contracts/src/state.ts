import { z } from "zod";

/** Job lifecycle. The only place transitions are defined; the API enforces them in SQL. */
export const JOB_STATES = [
  "CREATED",
  "UPLOADING",
  "QUEUED",
  "PREPROCESSING",
  "RECONSTRUCTING",
  "EVALUATING",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
] as const;
export const JobStateSchema = z.enum(JOB_STATES);
export type JobState = z.infer<typeof JobStateSchema>;

export const TERMINAL_STATES: readonly JobState[] = ["COMPLETED", "FAILED", "CANCELLED"];

/**
 * A retry re-enters QUEUED (the next attempt waits for the single GPU/CPU-heavy slot again).
 * Terminal states have no outgoing edges: a finished job is immutable; re-running creates a new job.
 */
export const TRANSITIONS: Record<JobState, readonly JobState[]> = {
  CREATED: ["UPLOADING", "QUEUED", "CANCELLED", "FAILED"],
  UPLOADING: ["QUEUED", "CANCELLED", "FAILED"],
  QUEUED: ["PREPROCESSING", "CANCELLED", "FAILED"],
  PREPROCESSING: ["RECONSTRUCTING", "QUEUED", "CANCELLED", "FAILED"],
  RECONSTRUCTING: ["EVALUATING", "QUEUED", "CANCELLED", "FAILED"],
  EVALUATING: ["COMPLETED", "QUEUED", "CANCELLED", "FAILED"],
  COMPLETED: [],
  FAILED: [],
  CANCELLED: [],
};

export function canTransition(from: JobState, to: JobState): boolean {
  return from === to || TRANSITIONS[from].includes(to);
}

export function isTerminal(state: JobState): boolean {
  return TERMINAL_STATES.includes(state);
}

/** States from which `to` may be entered; used to build `UPDATE ... WHERE state IN (...)`. */
export function predecessors(to: JobState): JobState[] {
  return JOB_STATES.filter((s) => TRANSITIONS[s].includes(to));
}
