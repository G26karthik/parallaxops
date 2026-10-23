# ADR 0004: Tenant isolation without a login system

Status: accepted (product decision)

## Context

The product owner does not want a login/signup system. The engineering brief still requires explicit
tenant scoping and tests that deny cross-tenant access.

## Decision

- Every API request carries a **tenant identity** (`X-Tenant-Id` header in local mode; the web app
  sends a per-browser tenant id it generates and stores locally). All tables carry `tenant_id`,
  every query is tenant-scoped in one data-access layer, and object keys are prefixed
  `tenants/<tenant>/...`.
- This is **isolation by scoping, not authentication**: anyone who knows another tenant's id can
  claim it. That is acceptable for a local single-user deployment and is documented as such.
- The seam for real auth is a single Fastify hook that resolves the tenant (header today; a verified
  JWT/OIDC claim later). Nothing else in the codebase reads the header.
- Presigned URLs are short-lived and bound to tenant-prefixed keys; the internal worker endpoint
  uses a shared secret that never reaches the browser.

## Consequences

- No passwords, sessions, OAuth or user table. Roadmap item: pluggable OIDC.
- Tests assert cross-tenant reads/downloads/cancels return 404, and that tenant ids are never
  accepted from request bodies.
