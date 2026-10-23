import { randomUUID, timingSafeEqual } from "node:crypto";
import { TenantIdSchema } from "@parallaxops/contracts";
import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import { hasZodFastifySchemaValidationErrors } from "fastify-type-provider-zod";
import type { Ctx } from "./context.ts";
import { AppError } from "./errors.ts";
import { ensureTenant } from "./services/jobs.ts";

declare module "fastify" {
  interface FastifyRequest {
    tenantId: string;
  }
}

const PUBLIC_PREFIXES = ["/healthz", "/readyz", "/metrics", "/docs", "/openapi.json", "/internal/"];
const REQUEST_ID = /^[A-Za-z0-9._-]{8,64}$/;

export function genReqId(req: { headers: Record<string, string | string[] | undefined> }): string {
  const h = req.headers["x-request-id"];
  return typeof h === "string" && REQUEST_ID.test(h) ? h : randomUUID();
}

export function registerHooks(app: FastifyInstance, ctx: Ctx) {
  const knownTenants = new Set<string>();

  app.addHook("onRequest", async (req: FastifyRequest, reply: FastifyReply) => {
    reply.header("x-request-id", req.id);
    reply.header("x-content-type-options", "nosniff");
    if (PUBLIC_PREFIXES.some((p) => req.url.startsWith(p))) return;
    if (req.method === "OPTIONS") return;
    // The tenant comes only from this header. Request bodies and query strings are never trusted for it.
    const raw = req.headers["x-tenant-id"];
    const parsed = TenantIdSchema.safeParse(Array.isArray(raw) ? raw[0] : raw);
    if (!parsed.success)
      throw new AppError(400, "TENANT_REQUIRED", "A valid X-Tenant-Id header is required.");
    req.tenantId = parsed.data;
    if (!knownTenants.has(parsed.data)) {
      await ensureTenant(ctx, parsed.data);
      knownTenants.add(parsed.data);
    }
  });

  app.addHook("onResponse", async (req, reply) => {
    ctx.metrics.httpDuration.observe(
      {
        method: req.method,
        route: req.routeOptions?.url ?? "unmatched",
        status: String(reply.statusCode),
      },
      reply.elapsedTime / 1000,
    );
  });

  app.setErrorHandler((err: unknown, req, reply) => {
    const e = err as { statusCode?: number; code?: string; message?: string };
    const base = { requestId: req.id };
    if (err instanceof AppError) {
      for (const [k, v] of Object.entries(err.headers ?? {})) reply.header(k, v);
      return reply.status(err.status).send({
        error: { code: err.code, message: err.message, details: err.details, ...base },
      });
    }
    if (hasZodFastifySchemaValidationErrors(err)) {
      return reply.status(400).send({
        error: {
          code: "VALIDATION_ERROR",
          message: "Request did not match the API contract.",
          details: err.validation.map((v) => ({ path: v.instancePath, message: v.message })),
          ...base,
        },
      });
    }
    if (e.statusCode && e.statusCode < 500) {
      return reply.status(e.statusCode).send({
        error: { code: e.code ?? "BAD_REQUEST", message: e.message ?? "Bad request", ...base },
      });
    }
    // Never leak internals; the request id links to the structured log line.
    req.log.error({ err }, "unhandled error");
    return reply
      .status(500)
      .send({ error: { code: "INTERNAL", message: "Internal server error.", ...base } });
  });

  app.setNotFoundHandler((req, reply) =>
    reply
      .status(404)
      .send({ error: { code: "NOT_FOUND", message: "Route not found.", requestId: req.id } }),
  );
}

/** Constant-time check of the worker's shared secret. */
export function requireInternalSecret(ctx: Ctx, req: FastifyRequest) {
  const given = req.headers["x-internal-secret"];
  const a = Buffer.from(typeof given === "string" ? given : "");
  const b = Buffer.from(ctx.cfg.INTERNAL_API_SECRET);
  if (a.length !== b.length || !timingSafeEqual(a, b))
    throw new AppError(401, "UNAUTHORIZED", "Internal endpoint requires the worker secret.");
}
