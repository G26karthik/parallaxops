import createClient from "openapi-fetch";
import type { paths } from "./api-types.ts";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:4000";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;
  constructor(status: number, code: string, message: string, requestId?: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export function apiClient(tenantId: string) {
  return createClient<paths>({ baseUrl: API_URL, headers: { "x-tenant-id": tenantId } });
}
export type Api = ReturnType<typeof apiClient>;

/** Unwraps an openapi-fetch result, turning the API's structured error into a thrown ApiError. */
export async function unwrap<T>(
  p: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await p;
  if (error !== undefined || data === undefined) {
    const e = (error as { error?: { code?: string; message?: string; requestId?: string } } | null)
      ?.error;
    throw new ApiError(
      response.status,
      e?.code ?? "HTTP_ERROR",
      e?.message ?? `Request failed (${response.status})`,
      e?.requestId,
    );
  }
  return data;
}
