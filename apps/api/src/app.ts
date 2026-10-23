import cors from "@fastify/cors";
import swagger from "@fastify/swagger";
import swaggerUi from "@fastify/swagger-ui";
import Fastify, { type FastifyInstance } from "fastify";
import {
  jsonSchemaTransform,
  serializerCompiler,
  validatorCompiler,
} from "fastify-type-provider-zod";
import type { Ctx } from "./context.ts";
import { genReqId, registerHooks } from "./plugins.ts";
import { jobRoutes } from "./routes/jobs.ts";
import { projectRoutes } from "./routes/projects.ts";
import { systemRoutes } from "./routes/system.ts";

export async function buildApp(
  ctx: Ctx,
  opts: { logger?: boolean } = {},
): Promise<FastifyInstance> {
  const app = Fastify({
    logger: opts.logger
      ? {
          level: ctx.cfg.LOG_LEVEL,
          redact: ["req.headers.authorization", 'req.headers["x-internal-secret"]'],
        }
      : false,
    genReqId,
    requestIdHeader: false,
    bodyLimit: 1_000_000,
  });
  app.setValidatorCompiler(validatorCompiler);
  app.setSerializerCompiler(serializerCompiler);

  await app.register(cors, {
    origin: ctx.cfg.corsOrigins,
    methods: ["GET", "POST", "DELETE", "OPTIONS"],
    allowedHeaders: ["content-type", "x-tenant-id", "idempotency-key", "x-request-id"],
    exposedHeaders: ["x-request-id", "location", "retry-after"],
    maxAge: 600,
  });
  await app.register(swagger, {
    openapi: {
      openapi: "3.1.0",
      info: {
        title: "ParallaxOps API",
        version: "1.0.0",
        description:
          "Control plane for distributed 3D reconstruction. All /v1 routes require an X-Tenant-Id " +
          "header (tenant scoping, not authentication; see ADR 0004).",
      },
      components: {
        securitySchemes: {
          tenant: { type: "apiKey", in: "header", name: "X-Tenant-Id" },
        },
      },
      security: [{ tenant: [] }],
    },
    transform: jsonSchemaTransform,
  });
  await app.register(swaggerUi, { routePrefix: "/docs" });
  app.get("/openapi.json", { schema: { hide: true } }, async () => app.swagger());

  registerHooks(app, ctx);
  projectRoutes(app, ctx);
  jobRoutes(app, ctx);
  systemRoutes(app, ctx);
  await app.ready();
  return app;
}
