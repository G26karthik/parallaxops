export class AppError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: unknown;
  readonly headers?: Record<string, string>;

  // No parameter properties: Node runs this file directly by stripping types (erasableSyntaxOnly).
  constructor(
    status: number,
    code: string,
    message: string,
    details?: unknown,
    headers?: Record<string, string>,
  ) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.headers = headers;
  }
}

export const notFound = (what: string) => new AppError(404, "NOT_FOUND", `${what} not found`);
export const conflict = (code: string, message: string, details?: unknown) =>
  new AppError(409, code, message, details);
export const badRequest = (code: string, message: string, details?: unknown) =>
  new AppError(400, code, message, details);
