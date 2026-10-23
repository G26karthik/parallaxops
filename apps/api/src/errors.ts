export class AppError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
    readonly headers?: Record<string, string>,
  ) {
    super(message);
  }
}

export const notFound = (what: string) => new AppError(404, "NOT_FOUND", `${what} not found`);
export const conflict = (code: string, message: string, details?: unknown) =>
  new AppError(409, code, message, details);
export const badRequest = (code: string, message: string, details?: unknown) =>
  new AppError(400, code, message, details);
