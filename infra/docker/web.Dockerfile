FROM node:26-slim
ENV PARALLAXOPS_HOME=/var/lib/parallaxops
RUN npm install -g pnpm@12.15.0
WORKDIR /app
COPY pnpm-workspace.yaml package.json pnpm-lock.yaml .npmrc ./
COPY packages ./packages
COPY apps/web ./apps/web
RUN pnpm install --frozen-lockfile --filter @parallaxops/web...
# The browser calls the API directly; bake its public URL in at build time.
ARG NEXT_PUBLIC_API_URL=http://localhost:4000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN pnpm --filter @parallaxops/web build
EXPOSE 3000
CMD ["pnpm", "--filter", "@parallaxops/web", "exec", "next", "start", "-p", "3000", "-H", "0.0.0.0"]
