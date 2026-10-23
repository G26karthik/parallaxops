# API image. Node runs the TypeScript sources directly (erasable syntax only), so there is no build step.
FROM node:26-slim
ENV PARALLAXOPS_HOME=/var/lib/parallaxops
RUN npm install -g pnpm@12.15.0
WORKDIR /app
COPY pnpm-workspace.yaml package.json pnpm-lock.yaml .npmrc ./
COPY packages ./packages
COPY apps/api ./apps/api
RUN pnpm install --frozen-lockfile --filter @parallaxops/api...
EXPOSE 4000
ENV HOST=0.0.0.0 PORT=4000 NODE_ENV=production
WORKDIR /app/apps/api
CMD ["node", "src/server.ts"]
