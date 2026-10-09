# syntax=docker/dockerfile:1
# Production image for the web UI: Next.js standalone output (minimal server.js + only the traced
# node_modules), multi-stage, non-root.
#
# API URL: the browser calls the same-origin path /api (NEXT_PUBLIC_API_URL, inlined at build
# time — host-independent, so the default needs no change). The Next server proxies /api/* to
# BPS_API_INTERNAL_URL, read at *runtime* (compose: http://api:8000). So one image works behind
# any hostname / reverse proxy, and the API needs no public port or CORS.

ARG NODE_IMAGE=node:26.11.1-alpine

FROM ${NODE_IMAGE} AS build

RUN npm install --global pnpm@12.10.1

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

COPY . .

ARG NEXT_PUBLIC_API_URL=/api
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_STANDALONE=1 \
    NEXT_TELEMETRY_DISABLED=1
RUN pnpm build


FROM ${NODE_IMAGE}

WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    BPS_API_INTERNAL_URL=http://api:8000

# server.js serves public/ and .next/static itself when they sit next to it.
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public

USER node
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/').then(r => process.exit(r.ok ? 0 : 1), () => process.exit(1))"]
CMD ["node", "server.js"]
